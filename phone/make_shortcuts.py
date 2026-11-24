"""Generate the two iPhone Shortcuts that talk to the Tripwire server.

  Tripwire Buy      record voice -> take photo -> POST /request (multipart) -> open /t/{token} in Safari
                    (live progress -> Approve -> merchant's Shop checkout with address + payment)
  Tripwire Approve  (optional, owner fallback; shows the LATEST pending cart) GET /pending -> Approve / Block -> POST /approve (JSON) -> show the reply -> open the Shop checkout

Usage (tailnet set for the owner's phone; public set for anyone, via Funnel on :10000):
  python3 phone/make_shortcuts.py https://<machine>.<tailnet>.ts.net:10000/tripwire phone/build/public
  python3 phone/make_shortcuts.py https://<machine>.<tailnet>.ts.net/tripwire
  python3 phone/make_shortcuts.py https://tripwire-labs.vercel.app phone/build/public   # hosted (v2.0): root, no /tripwire
  for n in "Tripwire Buy" "Tripwire Approve"; do
    shortcuts sign --mode anyone -i "phone/build/$n.unsigned.shortcut" -o "phone/build/$n.shortcut"; done
  open "phone/build/Tripwire Buy.shortcut"   # then "Tripwire Approve"; delete old copies first (re-import makes "Name 1")

Shortcut name = Siri phrase ("Hey Siri, Tripwire Buy").
"""
from __future__ import annotations

import pathlib
import plistlib
import sys
import uuid

OUT = pathlib.Path(__file__).parent / "build"
VERSION = "2.0"  # bump on every rebuild; Buy sends it as form field `client`, the page shows it
# 1.0 first build · 1.1 file upload fix · 1.2 Buy opens /t/{token} · 1.3 version field, delivered-price quote · 1.4 shopper can raise budget · 1.6 'Check Shopify' Siri name · 1.7 off-catalog Shopify links · 2.0 hosted on Vercel (laptop off), photo shrunk before upload (platform body limit 4.5 MB)


def new_id() -> str:
    return str(uuid.uuid4()).upper()


def output_ref(uid: str, name: str) -> dict:
    return {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}


def text(s: str) -> dict:
    return {"Value": {"string": s, "attachmentsByRange": {}}, "WFSerializationType": "WFTextTokenString"}


def token_text(*parts) -> dict:
    """parts: plain strings or output_ref dicts, concatenated into one text field."""
    out, attachments = "", {}
    for p in parts:
        if isinstance(p, str):
            out += p
        else:
            attachments[f"{{{len(out)}, 1}}"] = p
            out += "￼"
    return {"Value": {"string": out, "attachmentsByRange": attachments}, "WFSerializationType": "WFTextTokenString"}


def variable(ref: dict) -> dict:
    return {"Value": ref, "WFSerializationType": "WFTextTokenAttachment"}


def file_param(ref: dict) -> dict:
    # Form-field files need BOTH layers: a WFTextTokenAttachment inside WFTokenAttachmentParameterState.
    # Without the outer layer `shortcuts sign` throws; without the inner one iOS sends the form with NO files.
    return {"Value": variable(ref), "WFSerializationType": "WFTokenAttachmentParameterState"}


def fields(items: list[tuple[str, int, dict]]) -> dict:
    """items: (key, item_type, value). item_type 0 = text, 5 = file."""
    return {"Value": {"WFDictionaryFieldValueItems": [
        {"WFItemType": t, "WFKey": text(k), "WFValue": v} for k, t, v in items]},
        "WFSerializationType": "WFDictionaryFieldValue"}


def action(ident: str, uid: str | None = None, **params) -> dict:
    if uid:
        params["UUID"] = uid
    return {"WFWorkflowActionIdentifier": f"is.workflow.actions.{ident}", "WFWorkflowActionParameters": params}


def get_key(src_uid: str, src_name: str, key: str, uid: str) -> dict:
    return action("getvalueforkey", uid, WFInput=variable(output_ref(src_uid, src_name)),
                  WFDictionaryKey=key, WFGetDictionaryValueType="Value")


def buy(base: str, name: str = "Tripwire Buy") -> list[dict]:
    rec, cam, small, jpg, post, speak, tok = (new_id() for _ in range(7))
    return [
        action("comment", WFCommentActionText=f"{name} v{VERSION} ({base})"),
        action("recordaudio", rec),
        action("takephoto", cam, WFCameraCaptureShowPreview=True),
        # Hosted requests are capped at 4.5 MB: shrink the photo (longest edge 1600 px) and send it as JPEG.
        action("image.resize", small, WFImage=variable(output_ref(cam, "Photo")),
               WFImageResizeKey="Longest Edge", WFImageResizeLength="1600"),
        action("image.convert", jpg, WFInput=variable(output_ref(small, "Resized Image")),
               WFImageFormat="JPEG", WFImageCompressionQuality=0.7),
        action("downloadurl", post, WFURL=f"{base}/request", WFHTTPMethod="POST", WFHTTPBodyType="Form",
               WFFormValues=fields([
                   ("audio", 5, file_param(output_ref(rec, "Recorded Audio"))),
                   ("photo", 5, file_param(output_ref(jpg, "Converted Image"))),
                   ("client", 0, text(f"{name} {VERSION}")),
               ])),
        get_key(post, "Contents of URL", "speak", speak),
        get_key(post, "Contents of URL", "token", tok),
        # Got a token: open the live page in Safari (progress -> Approve -> the merchant's Shop checkout, where the
        # shopper sees address + payment and pays). No token: show why (empty upload, busy, budget).
        *if_else(output_ref(tok, "Dictionary Value"),
                 [action("openurl", WFInput=token_text(f"{base}/t/", output_ref(tok, "Dictionary Value")))],
                 [action("showresult", Text=token_text(output_ref(speak, "Dictionary Value")))]),
    ]


def if_else(ref: dict, then: list[dict], otherwise: list[dict]) -> list[dict]:
    gid = new_id()
    return [
        action("conditional", GroupingIdentifier=gid, WFControlFlowMode=0, WFCondition=100,
               WFInput={"Type": "Variable", "Variable": variable(ref)}),
        *then,
        action("conditional", GroupingIdentifier=gid, WFControlFlowMode=1),
        *otherwise,
        action("conditional", GroupingIdentifier=gid, WFControlFlowMode=2),
    ]


def if_has_value(ref: dict, body: list[dict]) -> list[dict]:
    """If <ref> has any value: body. End If."""
    gid = new_id()
    return [
        action("conditional", GroupingIdentifier=gid, WFControlFlowMode=0, WFCondition=100,
               WFInput={"Type": "Variable", "Variable": variable(ref)}),
        *body,
        action("conditional", GroupingIdentifier=gid, WFControlFlowMode=2),
    ]


def approve(base: str) -> list[dict]:
    get, cart, summ, reason, lst, pick, post, speak, url = (new_id() for _ in range(9))
    return [
        action("comment", WFCommentActionText=f"Tripwire Approve v{VERSION} ({base})"),
        action("downloadurl", get, WFURL=f"{base}/pending", WFHTTPMethod="GET"),
        get_key(get, "Contents of URL", "cart_id", cart),
        get_key(get, "Contents of URL", "summary", summ),
        get_key(get, "Contents of URL", "reason", reason),
        action("list", lst, WFItems=["Approve", "Block"]),
        action("choosefromlist", pick, WFInput=variable(output_ref(lst, "List")),
               WFChooseFromListActionPrompt=token_text(output_ref(summ, "Dictionary Value"), "\n",
                                                       output_ref(reason, "Dictionary Value"))),
        action("downloadurl", post, WFURL=f"{base}/approve", WFHTTPMethod="POST", WFHTTPBodyType="JSON",
               WFJSONValues=fields([
                   ("cart_id", 0, token_text(output_ref(cart, "Dictionary Value"))),
                   ("decision", 0, token_text(output_ref(pick, "Chosen Item"))),
               ])),
        get_key(post, "Contents of URL", "speak", speak),
        action("showresult", Text=token_text(output_ref(speak, "Dictionary Value"))),
        get_key(post, "Contents of URL", "url", url),
        # Approved: open the real Shop checkout so the shopper can pay with Shop Pay (link kept exactly as returned).
        *if_has_value(output_ref(url, "Dictionary Value"),
                      [action("openurl", WFInput=token_text(output_ref(url, "Dictionary Value")))]),
    ]


def workflow(actions: list[dict], color: int, glyph: int) -> dict:
    return {
        "WFWorkflowActions": actions,
        "WFWorkflowClientVersion": "2607.0.2",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": color, "WFWorkflowIconGlyphNumber": glyph},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowTypes": [],
        "WFQuickActionSurfaces": [],
        "WFWorkflowHasShortcutInputVariables": False,
    }


def main() -> None:
    if len(sys.argv) not in (2, 3) or not sys.argv[1].startswith("https://"):
        sys.exit("usage: make_shortcuts.py https://<host>[/tripwire] [out_dir]")
    base = sys.argv[1].rstrip("/")
    if any(ch.isspace() for ch in base):
        sys.exit(f"bad base URL {base!r}: must be https://<host>[:port][/tripwire] with no spaces")
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) == 3 else OUT
    out.mkdir(parents=True, exist_ok=True)
    # The shortcut's name is its Siri phrase: "Hey Siri, Check Shopify" runs the same flow as Tripwire Buy.
    for name, acts, color, glyph in (("Tripwire Buy", buy(base), 4282601983, 59511),
                                      ("Check Shopify", buy(base, "Check Shopify"), 4282601983, 59511),
                                      ("Tripwire Approve", approve(base), 4292093695, 59446)):
        path = out / f"{name}.unsigned.shortcut"
        path.write_bytes(plistlib.dumps(workflow(acts, color, glyph), fmt=plistlib.FMT_BINARY))
        print("wrote", path)


if __name__ == "__main__":
    main()
