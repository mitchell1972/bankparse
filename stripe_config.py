"""
BankParse — Stripe Configuration
Run this once to create products and prices in your Stripe account:
    python stripe_config.py
"""

import os
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

PLANS = {
    "starter": {
        "name": "BankScan AI Starter",
        "description": "100 scans/month, bulk upload (5 files), AI parsing, auto-insights, pre-built reports.",
        "price_gbp": 999,  # £9.99 in pence
        "lookup_key": "bankscan_starter_monthly",
    },
    "pro": {
        "name": "BankScan AI Pro",
        "description": "500 scans/month, bulk upload (20 files), AI parsing, auto-insights, pre-built reports.",
        "price_gbp": 2999,  # £29.99 in pence
        "lookup_key": "bankscan_pro_monthly",
    },
    "business": {
        "name": "BankScan AI Business",
        "description": "2,000 scans/month, bulk upload (50 files), AI parsing, auto-insights, reports, AI Chat (50/day).",
        "price_gbp": 7999,  # £79.99 in pence
        "lookup_key": "bankscan_business_monthly",
    },
    "enterprise": {
        "name": "BankScan AI Enterprise",
        "description": "Unlimited scans, bulk upload (100 files), AI parsing, auto-insights, reports, unlimited AI Chat.",
        "price_gbp": 19900,  # £199 in pence
        "lookup_key": "bankscan_enterprise_monthly",
    },
}


def create_products_and_prices():
    """Create BankParse products and prices in Stripe."""

    if not stripe.api_key:
        print("ERROR: Set STRIPE_SECRET_KEY environment variable first.")
        print("  export STRIPE_SECRET_KEY=sk_test_...")
        return

    for plan_id, plan in PLANS.items():
        # Check if product already exists
        existing = stripe.Product.search(query=f'name:"{plan["name"]}"')
        if existing.data:
            product = existing.data[0]
            print(f"Product '{plan['name']}' already exists: {product.id}")
        else:
            product = stripe.Product.create(
                name=plan["name"],
                description=plan["description"],
                metadata={"bankparse_plan": plan_id},
            )
            print(f"Created product '{plan['name']}': {product.id}")

        # Check if price already exists
        existing_prices = stripe.Price.search(
            query=f'lookup_key:"{plan["lookup_key"]}"'
        )
        if existing_prices.data:
            price = existing_prices.data[0]
            print(f"  Price already exists: {price.id} (£{price.unit_amount / 100:.2f}/mo)")
        else:
            price = stripe.Price.create(
                product=product.id,
                unit_amount=plan["price_gbp"],
                currency="gbp",
                recurring={"interval": "month"},
                lookup_key=plan["lookup_key"],
            )
            print(f"  Created price: {price.id} (£{price.unit_amount / 100:.2f}/mo)")

    print("\nDone! Copy the price IDs into your environment variables:")
    print("  STRIPE_STARTER_PRICE_ID=price_xxx")
    print("  STRIPE_PRO_PRICE_ID=price_xxx")
    print("  STRIPE_BUSINESS_PRICE_ID=price_xxx")
    print("  STRIPE_ENTERPRISE_PRICE_ID=price_xxx")


if __name__ == "__main__":
    create_products_and_prices()
