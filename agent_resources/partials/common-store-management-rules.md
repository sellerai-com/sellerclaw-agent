## Connected stores

A **sales channel** is one **connected storefront** in SellerClaw that the owner linked so you can call that **platform’s APIs** through SellerClaw using a stable `**store_id`** (**UUID**, same as `**sales_channel_id`** in APIs). Different channels can be **different platforms** (`shopify`, `ebay`, …); **stay inside this agent’s platform**—ignore channels whose `**platform`** does not match.

**Before any store-scoped task**, fix `**store_id`** for the exact shop you act on.

**If the shop is unnamed or only hinted** (nickname, domain fragment, “my store”, old task context): take `**store_id`** from the delegated payload or session context when present; otherwise infer—**exactly one** active channel for **this** platform and no conflicting hint → use its `**id`**; **none** → blocker (nothing connected); **several** plausible → blocker or ask upstream with candidate `**id` / name / domain**. Do **not** treat marketplace hostname or native seller ids as `**store_id`**.

Use the `sales-channels` skill  when you must list or filter channels to decide.