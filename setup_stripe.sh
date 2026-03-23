#!/bin/bash
# ============================================================
# BankScan AI — Stripe Setup
# Sets Vercel environment variables for the 4-tier pricing.
#
# Stripe prices are already created:
#   Starter:    price_1TECfoLniIk7TL9BGPes3d0Q  (£7.99/mo)
#   Pro:        price_1TECfoLniIk7TL9BA79kuFas  (£24.99/mo)
#   Business:   price_1TECfoLniIk7TL9BYNENKkva  (£59.99/mo)
#   Enterprise: price_1TECfpLniIk7TL9Bvd78vTgy  (£149/mo)
#
# Usage:
#   export STRIPE_SECRET_KEY=sk_test_xxx   (from dashboard.stripe.com/apikeys)
#   bash setup_stripe.sh
# ============================================================

set -e

if [ -z "$STRIPE_SECRET_KEY" ]; then
    echo "ERROR: Set STRIPE_SECRET_KEY first."
    echo "  1. Go to: https://dashboard.stripe.com/apikeys"
    echo "  2. Copy your Secret Key"
    echo "  3. Run: export STRIPE_SECRET_KEY=sk_test_xxx"
    echo "  4. Then run this script again."
    exit 1
fi

# Price IDs (already created in Stripe)
STARTER_PRICE="price_1TECfoLniIk7TL9BGPes3d0Q"
PRO_PRICE="price_1TECfoLniIk7TL9BA79kuFas"
BIZ_PRICE="price_1TECfoLniIk7TL9BYNENKkva"
ENT_PRICE="price_1TECfpLniIk7TL9Bvd78vTgy"

# Get publishable key
echo ""
echo "=== Stripe Publishable Key ==="
echo "  Get your Publishable Key from: https://dashboard.stripe.com/apikeys"
read -p "  Paste your Publishable Key (pk_test_xxx or pk_live_xxx): " STRIPE_PK

echo ""
echo "============================================================"
echo "  BANKSCAN AI — STRIPE PRICING"
echo "============================================================"
echo ""
echo "  Starter:    $STARTER_PRICE (£7.99/mo — 120 statements, 500 receipts)"
echo "  Pro:        $PRO_PRICE (£24.99/mo — 300 statements, 1,500 receipts)"
echo "  Business:   $BIZ_PRICE (£59.99/mo — 840 statements, 5,000 receipts)"
echo "  Enterprise: $ENT_PRICE (£149/mo — unlimited)"
echo ""

# Set Vercel env vars
echo "=== Setting Vercel environment variables ==="
cd "$(dirname "$0")"

echo "$STRIPE_SECRET_KEY" | vercel env add STRIPE_SECRET_KEY production --scope upwork-product --yes 2>/dev/null && echo "  + STRIPE_SECRET_KEY" || echo "  ! STRIPE_SECRET_KEY (may already exist)"
echo "$STRIPE_PK" | vercel env add STRIPE_PUBLISHABLE_KEY production --scope upwork-product --yes 2>/dev/null && echo "  + STRIPE_PUBLISHABLE_KEY" || echo "  ! STRIPE_PUBLISHABLE_KEY (may already exist)"
echo "$STARTER_PRICE" | vercel env add STRIPE_STARTER_PRICE_ID production --scope upwork-product --yes 2>/dev/null && echo "  + STRIPE_STARTER_PRICE_ID" || echo "  ! STRIPE_STARTER_PRICE_ID (may already exist)"
echo "$PRO_PRICE" | vercel env add STRIPE_PRO_PRICE_ID production --scope upwork-product --yes 2>/dev/null && echo "  + STRIPE_PRO_PRICE_ID" || echo "  ! STRIPE_PRO_PRICE_ID (may already exist)"
echo "$BIZ_PRICE" | vercel env add STRIPE_BUSINESS_PRICE_ID production --scope upwork-product --yes 2>/dev/null && echo "  + STRIPE_BUSINESS_PRICE_ID" || echo "  ! STRIPE_BUSINESS_PRICE_ID (may already exist)"
echo "$ENT_PRICE" | vercel env add STRIPE_ENTERPRISE_PRICE_ID production --scope upwork-product --yes 2>/dev/null && echo "  + STRIPE_ENTERPRISE_PRICE_ID" || echo "  ! STRIPE_ENTERPRISE_PRICE_ID (may already exist)"

echo ""
echo "============================================================"
echo "  SETUP COMPLETE"
echo "============================================================"
echo ""
echo "  Next steps:"
echo "  1. Create a webhook in Stripe Dashboard:"
echo "     URL: https://bankparse-pi.vercel.app/api/stripe-webhook"
echo "     Events: customer.subscription.updated, customer.subscription.deleted"
echo ""
echo "  2. Copy the webhook signing secret (whsec_xxx) and run:"
echo "     echo 'whsec_xxx' | vercel env add STRIPE_WEBHOOK_SECRET production --scope upwork-product"
echo ""
echo "  3. Redeploy to pick up env vars:"
echo "     vercel --prod --yes --scope upwork-product"
echo ""
echo "  4. Test: visit https://bankparse-pi.vercel.app"
echo ""

# Save to .env for local dev
cat > .env << ENVEOF
STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY
STRIPE_PUBLISHABLE_KEY=$STRIPE_PK
STRIPE_STARTER_PRICE_ID=$STARTER_PRICE
STRIPE_PRO_PRICE_ID=$PRO_PRICE
STRIPE_BUSINESS_PRICE_ID=$BIZ_PRICE
STRIPE_ENTERPRISE_PRICE_ID=$ENT_PRICE
ENVEOF
echo "  Local .env file created for development."
echo ""
