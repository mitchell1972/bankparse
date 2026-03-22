#!/bin/bash
# ============================================================
# BankParse — One-command Stripe Setup
# Creates products, prices, and sets Vercel environment vars.
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

echo "=== Creating BankParse Pro product ==="
PRO_PRODUCT=$(curl -s https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -d "name=BankParse Pro" \
  -d "description=Unlimited bank statement & receipt conversions. Priority support." \
  -d "metadata[bankparse_plan]=pro" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "  Product: $PRO_PRODUCT"

echo "=== Creating BankParse Pro price (£9.99/mo) ==="
PRO_PRICE=$(curl -s https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d "product=$PRO_PRODUCT" \
  -d "unit_amount=999" \
  -d "currency=gbp" \
  -d "recurring[interval]=month" \
  -d "lookup_key=bankparse_pro_monthly" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "  Price: $PRO_PRICE"

echo ""
echo "=== Creating BankParse Business product ==="
BIZ_PRODUCT=$(curl -s https://api.stripe.com/v1/products \
  -u "$STRIPE_SECRET_KEY:" \
  -d "name=BankParse Business" \
  -d "description=Unlimited conversions + API access + batch upload. For accountants & bookkeepers." \
  -d "metadata[bankparse_plan]=business" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "  Product: $BIZ_PRODUCT"

echo "=== Creating BankParse Business price (£29.99/mo) ==="
BIZ_PRICE=$(curl -s https://api.stripe.com/v1/prices \
  -u "$STRIPE_SECRET_KEY:" \
  -d "product=$BIZ_PRODUCT" \
  -d "unit_amount=2999" \
  -d "currency=gbp" \
  -d "recurring[interval]=month" \
  -d "lookup_key=bankparse_business_monthly" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
echo "  Price: $BIZ_PRICE"

# Get publishable key
echo ""
echo "=== Fetching Publishable Key ==="
echo "  Get your Publishable Key from: https://dashboard.stripe.com/apikeys"
read -p "  Paste your Publishable Key (pk_test_xxx or pk_live_xxx): " STRIPE_PK

echo ""
echo "============================================================"
echo "  STRIPE PRODUCTS CREATED SUCCESSFULLY"
echo "============================================================"
echo ""
echo "  Pro Product:    $PRO_PRODUCT"
echo "  Pro Price:      $PRO_PRICE (£9.99/mo)"
echo "  Business Prod:  $BIZ_PRODUCT"
echo "  Business Price: $BIZ_PRICE (£29.99/mo)"
echo ""

# Set Vercel env vars
echo "=== Setting Vercel environment variables ==="
cd "$(dirname "$0")"

echo "$STRIPE_SECRET_KEY" | vercel env add STRIPE_SECRET_KEY production --scope upwork-product --yes 2>/dev/null && echo "  ✓ STRIPE_SECRET_KEY" || echo "  ⚠ STRIPE_SECRET_KEY (may already exist)"
echo "$STRIPE_PK" | vercel env add STRIPE_PUBLISHABLE_KEY production --scope upwork-product --yes 2>/dev/null && echo "  ✓ STRIPE_PUBLISHABLE_KEY" || echo "  ⚠ STRIPE_PUBLISHABLE_KEY (may already exist)"
echo "$PRO_PRICE" | vercel env add STRIPE_PRO_PRICE_ID production --scope upwork-product --yes 2>/dev/null && echo "  ✓ STRIPE_PRO_PRICE_ID" || echo "  ⚠ STRIPE_PRO_PRICE_ID (may already exist)"
echo "$BIZ_PRICE" | vercel env add STRIPE_BUSINESS_PRICE_ID production --scope upwork-product --yes 2>/dev/null && echo "  ✓ STRIPE_BUSINESS_PRICE_ID" || echo "  ⚠ STRIPE_BUSINESS_PRICE_ID (may already exist)"

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
STRIPE_PRO_PRICE_ID=$PRO_PRICE
STRIPE_BUSINESS_PRICE_ID=$BIZ_PRICE
ENVEOF
echo "  Local .env file created for development."
echo ""
