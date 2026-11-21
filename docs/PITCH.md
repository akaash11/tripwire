# Pitch notes

**One line:** Tripwire turns what you asked for into a signed contract, and an AI shopping agent can only reach a real
Shopify checkout if the cart satisfies it.

**Hook:** an AI agent was tricked into moving money because the payment layer trusted the model's output.

## The question we will get
> "Shopify already makes the human open the checkout and press Pay. Why do I need a payment firewall?"

1. **People rubber-stamp checkout pages.** A one-tap Shop Pay sheet won't tell you the cable is 60W not 100W, that it
   doesn't look like the photo you took, or that a `final_sale` warning is buried. Tripwire catches these
   rule-passing traps *before* a checkout ever reaches your phone, and tells you why in one line.
2. **It's the authorization boundary for autonomous commerce.** Today Tripwire gates `create_checkout` and hands the
   shopper `continue_url` (Shopify's rule). The same signed Intent Contract + guard is what makes delegated payment
   (spend permissions, agent-completed checkout) safe tomorrow.
3. **The agent never holds a payment link.** Even a fully hijacked agent can only ask; Tripwire decides.

## Proof (5 Sigma)
No guard vs rules-only guard vs Tripwire, on Carbon-generated + outside attacks and tricky legit purchases. Attack
success, false positives, latency, and the cases Tripwire still misses (e.g. Decision 1 scored an obvious planted
instruction at only 0.28 injection, which is why escalation + canary exist).

## Live demo order
Phone voice + photo → contract on screen (~11s, Neon) → agent shops live (~20s, each step visible) → Decisions clause
bars (<1s) → Tripwire OFF buys the poisoned item / ON blocks it with the X-ray → judge approves a legit item on their
own phone at `/buy` → real Shop checkout opens → judge pays with Shop Pay.
