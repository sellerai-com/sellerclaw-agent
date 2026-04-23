---
name: product-data-api
description: "Read product catalog data for ad campaign planning and optimization."
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
