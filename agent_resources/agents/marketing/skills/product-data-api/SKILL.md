---
name: product-data-api
description: "Read SellerClaw catalog product data (name, description, images, price, stock, variations, status) needed to plan or optimize ad campaigns — list products with `GET /products` or fetch one with `GET /products/{id}`. Use before creating a campaign, scaling spend, or refreshing creative; also use to gate launches/scaling on stock and product status. Read-only — do not use for catalog edits or storefront listing changes."
---

# Product Data API Skill

## Goal
Fetch current product data directly from SellerClaw API to build better ad decisions.

## Endpoints

### `GET /products`

Returns a list of products for the authenticated user.

Important fields:
- `id`
- `name`
- `description`
- `images`
- `price`
- `status`
- `variations` (includes `stock`/quantity fields when available)

### `GET /products/{product_id}`

Returns one product with full details.

Use this endpoint before campaign creation, scaling, and creative refresh.

## Usage notes
- Prefer direct API reads over asking supervisor for product payload copies.
- Check stock before scaling budgets.
- If product is out of stock or inactive, flag risk and avoid launching/scaling spend.
