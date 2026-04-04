"""
Programmatic SEO — Data-driven page generation for long-tail search traffic.

Generates 750+ unique landing pages across 9 URL patterns:
  /tools/convert-{bank}-statement-to-excel
  /tools/bank-statement-converter-for-{profession}
  /tools/receipt-scanner-for-{profession}
  /tools/{bank}-pdf-to-{format}
  /tools/import-bank-statement-to-{software}
  /tools/bank-statement-for-{use-case}
  /tools/{bank}-statement-for-{profession}
  /tools/import-{bank}-to-{software}
  /tools/{bank}-statement-for-{use-case}

Each page gets unique title, description, H1, body content, FAQs,
and JSON-LD structured data for rich snippets.
"""

# ---------------------------------------------------------------------------
# UK Banks — names, slug fragments, and per-bank details
# ---------------------------------------------------------------------------
BANKS = {
    "hsbc": {
        "name": "HSBC",
        "full_name": "HSBC UK",
        "statement_notes": "HSBC statements often use multi-line transaction descriptions and older PDF formats that generic converters struggle with.",
        "formats": ["PDF", "scanned PDF"],
        "popular": True,
        "date_format": "DD MMM YYYY",
        "column_layout": "single amount column with DR/CR indicators and running balance",
        "common_issues": "Multi-line merchant descriptions that wrap across two rows, older PDF encryption that blocks text extraction, and transaction codes embedded before the description text",
        "digital_banking_tip": "In HSBC online banking, go to 'Statements' under your account, select the date range, and choose 'Download PDF'. For older statements, use the 'Previous statements' archive section.",
        "unique_features": [
            "Transaction type codes (BGC, TFR, DD, SO) appear as prefixes before descriptions",
            "Running balance resets at the top of each page in multi-page statements",
            "International HSBC transfers show both originating and receiving currency amounts",
            "Older HSBC statements (pre-2018) use a completely different layout from current ones",
        ],
        "typical_users": "Used by a broad range of UK personal and business customers, particularly popular with international professionals due to HSBC's global network and Premier banking services",
        "history_note": "HSBC UK is part of the global HSBC Holdings group. Their statement format has changed significantly since 2018, so older archived statements require different parsing rules than current ones.",
    },
    "barclays": {
        "name": "Barclays",
        "full_name": "Barclays Bank UK",
        "statement_notes": "Barclays statements come in a clean tabular layout, but date formats and running balances can trip up basic parsers.",
        "formats": ["PDF", "CSV"],
        "popular": True,
        "date_format": "DD/MM/YYYY",
        "column_layout": "separate Money In and Money Out columns with a running balance column",
        "common_issues": "Date column sometimes omits the year on continuation pages, pending transactions appear without amounts, and Barclays Pingit/transfers use truncated references",
        "digital_banking_tip": "In the Barclays app, tap your account, then 'Statements & documents'. You can download PDF statements by month. For CSV exports, use Barclays Online Banking on desktop via 'Export transactions'.",
        "unique_features": [
            "Separate 'Money In' and 'Money Out' columns rather than a single amount column",
            "Direct Debit originator names are shown alongside the reference number",
            "Barclays includes a daily balance summary at the end of each statement",
            "Business account statements include a separate charges page",
        ],
        "typical_users": "One of the UK's Big Four, popular with both personal customers and SMEs. Widely used by traditional business customers who value high-street branch access alongside digital banking",
        "history_note": "Barclays has been operating since 1690 and was a pioneer of UK ATM banking. Their statement format is one of the most commonly encountered by UK accountants and bookkeepers.",
    },
    "lloyds": {
        "name": "Lloyds",
        "full_name": "Lloyds Banking Group",
        "statement_notes": "Lloyds PDF statements use a consistent column layout, but multi-page statements sometimes split transactions across page breaks.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "DD MMM YY",
        "column_layout": "single Payment/Receipt column with type indicators and running balance",
        "common_issues": "Transactions split across page breaks lose their date on the continuation page, standing order descriptions are truncated to 18 characters, and the balance brought forward row can be mistaken for a transaction",
        "digital_banking_tip": "Log in to Lloyds Internet Banking, select your account, click 'View statements', choose the period and download as PDF. CSV export is available under 'Download transactions' with custom date ranges.",
        "unique_features": [
            "Transaction types (DD, SO, BGC, FPO, FPI) are listed in a dedicated 'Type' column",
            "Balance brought forward appears as the first row and must be excluded from transaction lists",
            "Multi-page statements repeat the column headers on each page",
            "Savings account and current account transactions may appear in a combined statement",
        ],
        "typical_users": "The UK's largest retail bank by current accounts. Popular with a wide demographic of UK personal customers, particularly those with long-standing accounts and mortgage relationships",
        "history_note": "Part of Lloyds Banking Group since the 2009 HBOS merger. Lloyds statements share structural DNA with Halifax and Bank of Scotland but have distinct header layouts and type code conventions.",
    },
    "natwest": {
        "name": "NatWest",
        "full_name": "NatWest Bank",
        "statement_notes": "NatWest statements include detailed transaction references that can cause column misalignment in generic converters.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "DD/MM/YYYY",
        "column_layout": "separate Paid In and Paid Out columns with a Balance column on the right",
        "common_issues": "Long transaction references overflow into the amount columns causing misalignment, Faster Payment references include sender sort codes that look like amounts, and branch-generated statements have different spacing than online ones",
        "digital_banking_tip": "In NatWest Online Banking, go to 'Statements', select your account and statement period, then click 'Download' and choose PDF format. For data exports, use 'Download transactions' with OFX or CSV options.",
        "unique_features": [
            "Paid In and Paid Out columns are clearly separated with the balance on the far right",
            "Transaction reference numbers can be up to 18 characters and include originator details",
            "Cheque numbers are displayed inline within the transaction description",
            "NatWest business statements include a separate page for standing order schedules",
        ],
        "typical_users": "Popular with UK personal and business customers, especially in England and Wales. Frequently used by SMEs and sole traders who prefer traditional banking with strong online capabilities",
        "history_note": "NatWest is part of NatWest Group (formerly RBS Group). NatWest and RBS statements share underlying formatting but NatWest uses different branding headers and occasionally different column widths.",
    },
    "monzo": {
        "name": "Monzo",
        "full_name": "Monzo Bank",
        "statement_notes": "Monzo provides CSV exports natively, but many clients still send PDF statements that need parsing for bookkeeping software.",
        "formats": ["PDF", "CSV"],
        "popular": True,
        "date_format": "DD/MM/YYYY",
        "column_layout": "single Amount column with positive/negative values and merchant category tags",
        "common_issues": "Pot transfers appear as internal transactions that inflate totals if not filtered, merchant names change between card payments and refunds for the same vendor, and transaction notes added by the user appear as extra lines",
        "digital_banking_tip": "In the Monzo app, go to your account, tap 'Account' tab, then 'Statement history' to download PDF statements by month. For CSV exports, contact Monzo in-app chat to request a data export.",
        "unique_features": [
            "Merchant category codes (e.g., Groceries, Transport, Entertainment) are included in the export",
            "Pot transfers to savings pots appear as internal debits/credits",
            "Notes and receipt images attached to transactions are referenced in PDF statements",
            "Running balance reflects real-time settlement rather than pending authorization amounts",
        ],
        "typical_users": "Popular with UK freelancers, millennials, and digital-first customers who prefer app-based banking. Increasingly adopted by small businesses using Monzo Business accounts",
        "history_note": "Founded in 2015 as one of the UK's first app-only challenger banks. Monzo's PDF statements are relatively straightforward since they were designed for digital-first export from day one.",
    },
    "starling": {
        "name": "Starling",
        "full_name": "Starling Bank",
        "statement_notes": "Starling Bank statements have a modern layout with merchant categories, which BankScan AI preserves during conversion.",
        "formats": ["PDF", "CSV"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "single Amount column with +/- signs, merchant categories, and running balance",
        "common_issues": "Multi-currency transactions show both original and GBP amounts in the same row, round-up savings appear as separate micro-transactions, and business expense categories add extra text to descriptions",
        "digital_banking_tip": "In the Starling app, go to 'Account', tap 'Statements', select the month, and download as PDF. For CSV, tap 'Settings' > 'Statement preferences' and enable CSV export format.",
        "unique_features": [
            "Merchant categories and spending insights are embedded in the statement layout",
            "Spaces (savings goals) transfers are listed with the Space name in the description",
            "Contactless, chip, and online payment methods are distinguished in transaction details",
            "Business accounts show VAT category tags alongside each transaction",
        ],
        "typical_users": "Favoured by tech-savvy UK consumers and small business owners who want a full-featured app-based bank. Popular with sole traders using Starling Business for fee-free banking",
        "history_note": "Founded in 2014 by former Allied Irish Banks COO Anne Boden. Starling was the first UK mobile bank to offer business accounts and has maintained a consistently clean statement format.",
    },
    "santander": {
        "name": "Santander",
        "full_name": "Santander UK",
        "statement_notes": "Santander UK statements use a two-column debit/credit layout that requires careful parsing to maintain accuracy.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "DD/MM/YYYY",
        "column_layout": "two-column debit/credit layout with separate Payments and Receipts columns",
        "common_issues": "Cashback rewards from the 1|2|3 account appear as credit entries that can be confused with refunds, international transactions show truncated merchant names, and the statement header includes account analytics that can interfere with table detection",
        "digital_banking_tip": "Log in to Santander Online Banking, select your account, go to 'Statements and documents', pick the statement date, and click 'Download PDF'. For CSV/OFX exports, use 'Download transactions' with a custom date range.",
        "unique_features": [
            "1|2|3 Current Account cashback entries appear as separate credit transactions",
            "Standing orders and direct debits are grouped in dedicated sections",
            "Monthly account fee charges appear as a separate line item",
            "Mortgage and savings interest summaries appear on the same statement as current account transactions",
        ],
        "typical_users": "Popular with UK customers attracted by cashback current accounts. Common among homeowners who bundle their current account with Santander mortgage products for better rates",
        "history_note": "Santander UK was formed from the acquisition of Abbey National (2004), Alliance & Leicester, and Bradford & Bingley. Legacy account statements from these predecessors may still appear in client archives.",
    },
    "nationwide": {
        "name": "Nationwide",
        "full_name": "Nationwide Building Society",
        "statement_notes": "Nationwide statements include both current account and savings in a single PDF, which BankScan AI separates automatically.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YYYY",
        "column_layout": "single Payments and Receipts column with transaction type codes and running balance",
        "common_issues": "Combined current account and savings transactions in a single PDF require separation, FlexAccount rewards entries appear as miscellaneous credits, and older mortgage-linked statements include repayment schedules that can confuse table parsers",
        "digital_banking_tip": "In Nationwide Internet Banking, go to 'My accounts', select your account, click 'Statements', choose the period and download as PDF. eStatements are available going back several years.",
        "unique_features": [
            "Building society heritage means statements may reference 'share accounts' rather than 'savings accounts'",
            "FlexPlus, FlexDirect, and FlexAccount benefits are itemised separately",
            "ISA and savings account transactions may appear alongside current account entries",
            "Monthly interest paid entries show the gross and net interest amounts",
        ],
        "typical_users": "Popular with UK savers and homeowners who value the mutual building society model. Common among customers with bundled savings, current account, and mortgage products",
        "history_note": "Nationwide is the world's largest building society, not a bank. This mutual ownership structure means their statements sometimes use different terminology (e.g., 'members' rather than 'customers').",
    },
    "rbs": {
        "name": "RBS",
        "full_name": "Royal Bank of Scotland",
        "statement_notes": "RBS statements share a similar format to NatWest. BankScan AI handles both with the same high accuracy.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "separate Paid In and Paid Out columns matching NatWest's layout with minor header differences",
        "common_issues": "Scottish pound references can appear in international transaction descriptions, legacy RBS formats from before the NatWest Group rebrand have different headers, and commercial banking statements use wider column spacing than personal ones",
        "digital_banking_tip": "In RBS Digital Banking, select your account, go to 'Statements', choose the date range, and download as PDF. Transaction exports in CSV or OFX are available under 'Download transactions'.",
        "unique_features": [
            "Layout closely mirrors NatWest but with RBS-specific header branding and sort code formats",
            "Scottish branch statements may include references to Scottish banknote issuance",
            "Commercial banking statements include a separate fee analysis page",
            "Reward account benefits are listed in a summary section at the bottom of each statement",
        ],
        "typical_users": "Primarily used by Scottish personal and business customers. Also common among commercial clients in the energy, financial services, and public sectors",
        "history_note": "Royal Bank of Scotland is part of NatWest Group (rebranded in 2020). RBS and NatWest statements share the same underlying data structure but use different branding and occasionally different column header names.",
    },
    "halifax": {
        "name": "Halifax",
        "full_name": "Halifax (Bank of Scotland)",
        "statement_notes": "Halifax statements use a Lloyds-family format with slight variations in header layout that BankScan AI automatically detects.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YY",
        "column_layout": "single column for debits and credits with a Type column and running balance",
        "common_issues": "Reward Account bonus entries appear as lump-sum credits without detailed breakdown, continuation pages drop the date column header, and the 'Balance brought forward' row format differs from transaction rows",
        "digital_banking_tip": "In Halifax Online Banking, click your account, select 'Statements', choose the statement date, and download as PDF. You can also export transactions as CSV by clicking 'Download your transactions'.",
        "unique_features": [
            "Reward Current Account bonus payments appear as annual credit entries",
            "Halifax uses the same transaction type codes as Lloyds (DD, SO, FPO, FPI, BGC)",
            "Ultimate Reward Account benefits are summarised in a separate section",
            "Statement headers include the full branch name and sort code details",
        ],
        "typical_users": "Popular with UK personal banking customers, especially those attracted by the Halifax Reward Current Account. Common among first-time buyers using Halifax mortgage products",
        "history_note": "Part of Lloyds Banking Group since the 2009 HBOS acquisition. Halifax statements share formatting DNA with Lloyds but have distinct header layouts, reward sections, and branding elements.",
    },
    "revolut": {
        "name": "Revolut",
        "full_name": "Revolut",
        "statement_notes": "Revolut statements include multi-currency transactions. BankScan AI preserves the original currency and exchange rate columns.",
        "formats": ["PDF", "CSV"],
        "popular": True,
        "date_format": "DD MMM YYYY",
        "column_layout": "single Amount column with currency code prefix, exchange rate details, and running balance per currency",
        "common_issues": "Multi-currency accounts generate separate transaction lists per currency that must be consolidated, vault savings transfers inflate transaction counts, and fee-free ATM withdrawal limits create separate fee lines when exceeded",
        "digital_banking_tip": "In the Revolut app, tap 'Statements' from your account screen, select the month and currency, then choose PDF or CSV format. For multi-currency accounts, you need to download statements per currency individually.",
        "unique_features": [
            "Each transaction shows the original currency amount and the GBP equivalent with the exchange rate used",
            "Vault transfers (savings) appear as paired debit/credit entries",
            "Cryptocurrency buy/sell transactions appear alongside regular banking transactions",
            "Premium and Metal subscription fees are listed as separate recurring transactions",
        ],
        "typical_users": "Popular with frequent travellers, expats, and international freelancers who need multi-currency accounts. Widely used by digital nomads and cross-border e-commerce sellers",
        "history_note": "Founded in 2015, Revolut received its UK banking licence in 2024. Earlier statements were issued under their e-money licence, which means older statements may have different regulatory headers.",
    },
    "tide": {
        "name": "Tide",
        "full_name": "Tide Business Banking",
        "statement_notes": "Tide business account statements include invoice references and categories that BankScan AI maps to your bookkeeping codes.",
        "formats": ["PDF", "CSV"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "single Amount column with category tags, invoice references, and running balance",
        "common_issues": "Automated invoice matching creates linked transaction references that add extra text to descriptions, expense category tags can fragment the description column, and multi-company account statements from the same login need to be separated",
        "digital_banking_tip": "In the Tide app, go to 'Account', tap 'Statements', select the month and download as PDF. For bookkeeping integrations, use Tide's direct CSV export under 'Transactions' > 'Export'.",
        "unique_features": [
            "Invoice references are automatically matched to incoming payments",
            "Expense categories are assigned to each transaction for bookkeeping",
            "Receipt photos attached to transactions are referenced in the PDF statement",
            "Multiple company accounts under one login generate separate statements per company",
        ],
        "typical_users": "Designed for UK sole traders, freelancers, and small businesses. Popular with contractors and micro-businesses who want integrated invoicing alongside banking",
        "history_note": "Founded in 2017 as a business-only challenger bank. Tide partners with ClearBank and Prepay Solutions for banking infrastructure, which means the underlying statement format has evolved as they changed providers.",
    },
    "metro-bank": {
        "name": "Metro Bank",
        "full_name": "Metro Bank",
        "statement_notes": "Metro Bank statements use a straightforward layout. BankScan AI converts them cleanly with full transaction detail preserved.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "two-column Payments In and Payments Out with running balance on the far right",
        "common_issues": "In-branch deposit descriptions include teller reference numbers that add noise to transaction descriptions, Safe Deposit box fees appear as banking transactions, and business account statements include separate interest calculation pages",
        "digital_banking_tip": "In Metro Bank Online Banking, go to 'Accounts', select your account, click 'Statements' and choose the month to download as PDF. Transaction exports are available under 'Download' in CSV or QIF format.",
        "unique_features": [
            "Branch deposit transactions include the specific store location name",
            "Safe Deposit Box rental fees appear as quarterly debit transactions",
            "Extended opening hours transactions show timestamps outside normal banking hours",
            "Business accounts include a separate page detailing interest rate tiers",
        ],
        "typical_users": "Attracts customers who value in-person service with extended branch hours including weekends. Popular with small businesses and retail operators in London and Southeast England",
        "history_note": "Founded in 2010 as the first new UK high-street bank in over 100 years. Metro Bank's statement format is relatively modern and consistent, having been designed from scratch without legacy system constraints.",
    },
    "tsb": {
        "name": "TSB",
        "full_name": "TSB Bank",
        "statement_notes": "TSB statements are formatted similarly to Lloyds. BankScan AI recognises the layout and applies the correct parsing rules.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YY",
        "column_layout": "single column with Type indicators, payment/receipt amounts, and running balance",
        "common_issues": "TSB Spend & Save cashback rewards appear as credit entries without a clear merchant description, the fraud refund guarantee entries use a unique format, and statements from the 2018 IT migration period may have duplicate or missing transactions",
        "digital_banking_tip": "In TSB Internet Banking, go to 'My accounts', select your account, click 'Statements', pick the date range and download as PDF. For transaction data export, use 'Download your transactions' in CSV format.",
        "unique_features": [
            "Spend & Save account cashback entries appear as monthly credit transactions",
            "TSB Fraud Refund Guarantee reversal entries have a distinctive format",
            "Transaction type codes follow the Lloyds convention (DD, SO, BGC, FPO)",
            "Interest rate summaries appear at the bottom of each monthly statement",
        ],
        "typical_users": "Attracts UK personal banking customers who value straightforward banking. Popular with customers who prefer a simpler product range compared to Big Four banks",
        "history_note": "TSB was re-established as a separate entity from Lloyds Banking Group in 2013 and later acquired by Sabadell Group. Their statements initially mirrored Lloyds format closely but have gradually diverged since the 2018 platform migration.",
    },
    "coutts": {
        "name": "Coutts",
        "full_name": "Coutts & Co",
        "statement_notes": "Coutts private banking statements have a premium layout with additional wealth summary sections that BankScan AI filters correctly.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YYYY",
        "column_layout": "premium layout with separate current account, savings, and investment summary sections",
        "common_issues": "Wealth management summary pages interspersed with transaction pages confuse table detection, multi-currency holdings appear alongside GBP transactions, and gold-embossed PDF formatting uses non-standard fonts",
        "digital_banking_tip": "In Coutts Online, navigate to 'Accounts & Statements', select the account, and download statements as PDF. Contact your private banker for consolidated statements covering all accounts and investment holdings.",
        "unique_features": [
            "Wealth summary pages showing portfolio valuations appear before transaction listings",
            "Multi-currency holdings are displayed alongside sterling transactions with real-time valuations",
            "Statements include a dedicated section for fees charged under the private banking relationship",
            "Charitable donation tracking is included for philanthropy-focused clients",
        ],
        "typical_users": "Serves high-net-worth individuals, landed estates, and high-profile clients. The bank requires a minimum income of 1 million GBP or 1 million GBP in investable assets to open an account",
        "history_note": "Founded in 1692, Coutts is the oldest private bank in the UK and banker to the British Royal Family. Their statement format reflects this heritage with a premium layout distinct from any other UK bank.",
    },
    "virgin-money": {
        "name": "Virgin Money",
        "full_name": "Virgin Money UK",
        "statement_notes": "Virgin Money statements (formerly Clydesdale/Yorkshire Bank) use a modern layout with transaction categories that BankScan AI preserves during conversion.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "modern single-column layout with colour-coded spending categories and running balance",
        "common_issues": "Legacy Clydesdale and Yorkshire Bank statement formats still appear for older accounts, Virgin Money reward entries use a different format from regular transactions, and the colourful PDF styling can interfere with text extraction",
        "digital_banking_tip": "In Virgin Money Online Banking, go to 'My accounts', select your account, click 'Statements' and download as PDF. For transaction exports, use the 'Download' button under your transaction list with CSV or OFX format options.",
        "unique_features": [
            "Spending categories with colour coding are embedded in the statement layout",
            "Virgin Points rewards earnings are shown alongside qualifying transactions",
            "Legacy Clydesdale/Yorkshire Bank account numbers may still appear in statement headers",
            "Club M and current account benefit claims appear as separate credit entries",
        ],
        "typical_users": "Popular with UK personal customers attracted by Virgin brand and rewards. Common among former Clydesdale and Yorkshire Bank customers who were migrated during the rebrand",
        "history_note": "Virgin Money acquired Clydesdale and Yorkshire Bank in 2018 and rebranded all accounts. Older statements may carry the legacy Clydesdale or Yorkshire Bank formatting, requiring different parsing rules from the current Virgin Money layout.",
    },
    "first-direct": {
        "name": "First Direct",
        "full_name": "First Direct (HSBC)",
        "statement_notes": "First Direct statements follow an HSBC-family format but with a distinct minimalist layout. BankScan AI detects the variant automatically.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YYYY",
        "column_layout": "minimalist single-column layout with transaction type codes and running balance, similar to HSBC but with simplified headers",
        "common_issues": "The minimalist layout omits some metadata present in standard HSBC statements, telephone banking transaction references use abbreviated codes, and the PDF uses a clean sans-serif font that differs from HSBC's standard",
        "digital_banking_tip": "Log in to First Direct Online Banking, go to 'Statements', select the account and date range, and download as PDF. First Direct also supports OFX export under 'Download transactions' for direct import into accounting software.",
        "unique_features": [
            "Minimalist header design distinct from parent HSBC despite sharing underlying transaction codes",
            "Regular Saver account transactions appear alongside current account entries",
            "Telephone banking-initiated transactions carry distinctive reference prefixes",
            "Linked savings account transfers show the destination account name",
        ],
        "typical_users": "Popular with digitally savvy UK customers who value high-quality telephone and online customer service. Often chosen by professionals who prefer a branchless banking experience",
        "history_note": "Launched in 1989 as the UK's first telephone-only bank, First Direct is a division of HSBC. Their statements share HSBC's underlying data structure but use a distinctive minimalist design that has remained consistent since their move to online banking.",
    },
    "co-op-bank": {
        "name": "Co-operative Bank",
        "full_name": "The Co-operative Bank",
        "statement_notes": "Co-operative Bank statements use a traditional tabular layout with running balances. BankScan AI handles the format reliably, including joint account statements.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "traditional three-column layout with separate Debit, Credit, and Balance columns",
        "common_issues": "Joint account statements show both account holder names in headers which can extend into the table area, ethical investment references add extra text to certain transactions, and older statements use a noticeably different font and spacing",
        "digital_banking_tip": "In Co-operative Bank Online Banking, click 'My accounts', select the account, go to 'Statements & documents', and download PDF statements by month. Transaction exports in CSV format are available under 'Download transactions'.",
        "unique_features": [
            "Ethical banking commitment references may appear in statement footers",
            "Joint account statements display both account holder names prominently",
            "Cashminder and Everyday Rewards entries appear as monthly credit transactions",
            "Community Directplus and charity donation tracking are referenced in business statements",
        ],
        "typical_users": "Chosen by ethically-minded UK customers who value the bank's ethical investment policy. Popular with charities, trade unions, and co-operative organisations",
        "history_note": "The Co-operative Bank has operated under an ethical banking policy since 1992, screening investments and lending against ethical criteria. Despite financial difficulties in 2013-2017, the bank maintained its ethical stance, and its statement format has remained stable through ownership changes.",
    },
    "bank-of-ireland": {
        "name": "Bank of Ireland",
        "full_name": "Bank of Ireland UK",
        "statement_notes": "Bank of Ireland UK statements use a layout common to Irish banking formats. BankScan AI parses both GB and NI account statement variants.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "two-column Debit and Credit layout with Balance column, following Irish banking conventions",
        "common_issues": "Northern Ireland and Republic of Ireland statements use slightly different layouts, GBP and EUR transactions may appear on the same statement for cross-border customers, and branch-generated statements have wider margins than online ones",
        "digital_banking_tip": "In Bank of Ireland 365 Online, select your account, click 'Statements', choose the period and download as PDF. For UK business accounts, use the BOI UK Business Online portal which offers CSV and OFX exports.",
        "unique_features": [
            "Cross-border GBP and EUR transactions may appear on the same statement for NI customers",
            "Sort codes follow the Northern Ireland format which differs from mainland UK conventions",
            "Standing order and direct debit mandates are listed in a separate section",
            "Post Office counter transactions carry distinctive reference codes",
        ],
        "typical_users": "Primarily serves Northern Ireland personal and business customers. Also used by customers with cross-border banking needs between the UK and Republic of Ireland",
        "history_note": "Bank of Ireland UK operates primarily in Northern Ireland, where it is one of four banks authorised to issue banknotes. Their UK statements follow Irish banking conventions that differ from mainland UK formats in column layout and date presentation.",
    },
    "danske-bank": {
        "name": "Danske Bank",
        "full_name": "Danske Bank UK",
        "statement_notes": "Danske Bank UK (Northern Ireland) statements use a Scandinavian-influenced layout. BankScan AI handles the unique date and amount formatting.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "Scandinavian-influenced layout with separate Debit and Credit columns and a daily balance summary",
        "common_issues": "Scandinavian formatting conventions occasionally appear in amount presentation, Northern Ireland-specific transaction codes differ from mainland UK banks, and business eBanking statements have a different layout from personal ones",
        "digital_banking_tip": "In Danske Bank eBanking, go to 'Accounts', select the account, click 'Statements' and download as PDF. Business customers can export transactions in CSV format from the eBanking platform under 'Account overview' > 'Export'.",
        "unique_features": [
            "Scandinavian parent bank influence is visible in statement design and layout conventions",
            "Northern Ireland banknote issuance references may appear on business account statements",
            "Daily balance summary rows are included between transaction entries",
            "Cross-border Sterling and Euro transaction handling reflects the NI-ROI border dynamic",
        ],
        "typical_users": "Primarily serves Northern Ireland personal and business customers. Strong presence in the NI agricultural and commercial sectors inherited from Northern Bank",
        "history_note": "Formerly Northern Bank until acquired by Danske Bank Group in 2004. The 2004 Northern Bank robbery (26.5 million GBP) led to complete banknote reissuance. Their statement format transitioned from Northern Bank's layout to a Scandinavian-influenced design after the acquisition.",
    },
    "aib": {
        "name": "AIB",
        "full_name": "AIB UK (Allied Irish Banks)",
        "statement_notes": "AIB UK statements serve Northern Ireland customers with a format that differs from mainland UK banks. BankScan AI parses both GBP and EUR transactions.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "Irish banking format with separate Debit and Credit columns plus running balance",
        "common_issues": "GBP and EUR transactions may be intermixed on cross-border accounts, AIB Group formatting conventions from the Republic of Ireland parent occasionally appear, and older First Trust Bank-branded statements use a completely different layout",
        "digital_banking_tip": "In AIB UK Internet Banking, select your account, click 'Statements', choose the period and download as PDF. For business accounts, the AIB UK Business Online platform offers CSV exports under 'Transaction history'.",
        "unique_features": [
            "Cross-border EUR transactions appear alongside GBP entries for customers with Republic of Ireland connections",
            "Former First Trust Bank branding may still appear on legacy account statements",
            "Agricultural lending and farm payment scheme references are common in rural NI accounts",
            "AIB UK sort codes follow the Northern Ireland banking convention",
        ],
        "typical_users": "Serves Northern Ireland personal and business customers, particularly strong in the agricultural sector and among businesses with Republic of Ireland trade connections",
        "history_note": "AIB UK operated as First Trust Bank in Northern Ireland until rebranding to AIB in 2019. Legacy First Trust Bank statements have a different format from current AIB-branded ones, and older client archives may contain both formats.",
    },
    "investec": {
        "name": "Investec",
        "full_name": "Investec Bank UK",
        "statement_notes": "Investec private banking statements include investment account summaries alongside current account transactions. BankScan AI separates and converts the banking transactions.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YYYY",
        "column_layout": "premium layout with separate banking and investment sections, each with their own column structures",
        "common_issues": "Investment portfolio summaries interspersed with banking transactions require careful section detection, South African Rand references appear for clients with SA connections, and the premium PDF formatting uses custom fonts that can affect text extraction",
        "digital_banking_tip": "In Investec Online, navigate to 'Banking', select your account, and download statements under 'Statements & documents'. For investment account summaries, use the 'Wealth & Investment' section separately.",
        "unique_features": [
            "Investment account valuations and banking transactions are combined in a single statement",
            "Private client fee schedules are itemised in a dedicated section",
            "Foreign currency accounts show ZAR, GBP, USD, and EUR balances side by side",
            "Professional client designations and relationship manager details appear in statement headers",
        ],
        "typical_users": "Serves high-net-worth professionals, entrepreneurs, and business owners seeking specialist private banking and wealth management. Popular with medical professionals, lawyers, and senior executives",
        "history_note": "Investec is a South African-origin specialist bank that established its UK operations in 1992. Their dual-listed structure (London and Johannesburg) means statements may reference both jurisdictions for clients with cross-border holdings.",
    },
    "handelsbanken": {
        "name": "Handelsbanken",
        "full_name": "Handelsbanken UK",
        "statement_notes": "Handelsbanken's relationship banking model means each branch produces slightly different statement layouts. BankScan AI adapts to all Handelsbanken UK variants.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "YYYY-MM-DD",
        "column_layout": "Scandinavian-style layout with ISO date format, single amount column with +/- signs, and running balance",
        "common_issues": "Branch-specific statement variations mean column widths and spacing differ between locations, the ISO date format (YYYY-MM-DD) is unusual for UK banks and can confuse parsers expecting DD/MM/YYYY, and Swedish-language headers occasionally appear in system-generated exports",
        "digital_banking_tip": "In Handelsbanken Online Banking, select your account, go to 'Account statements', choose the period and download as PDF. Each branch manages its own customer relationships, so contact your branch directly for historic or consolidated statements.",
        "unique_features": [
            "ISO 8601 date format (YYYY-MM-DD) is used instead of UK-standard DD/MM/YYYY",
            "Branch-specific formatting means statements from different Handelsbanken locations may look slightly different",
            "Property finance and commercial lending statements use a separate detailed format",
            "Relationship manager name and direct contact details appear on each statement",
        ],
        "typical_users": "Serves UK business owners and professionals who value a relationship banking approach with dedicated local branch managers. Popular among property investors and SMEs seeking personalised commercial banking",
        "history_note": "Handelsbanken is Sweden's largest bank and has operated in the UK since 1982. Their decentralised branch model means each branch operates semi-autonomously, which historically led to slight statement format variations between locations.",
    },
    "atom-bank": {
        "name": "Atom Bank",
        "full_name": "Atom Bank",
        "statement_notes": "Atom Bank, the UK's first app-only bank, produces digital-first PDF statements with a clean layout that BankScan AI converts with high accuracy.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "clean digital-first single-column layout with amount and running balance",
        "common_issues": "Fixed saver maturity entries can be confused with regular transactions, the app-generated PDF uses modern font rendering that occasionally affects text extraction, and mortgage-related statements have a completely different layout from savings",
        "digital_banking_tip": "In the Atom Bank app, go to your account, tap 'Documents' or 'Statements', and download the PDF statement for the desired period. Atom Bank is app-only, so there is no desktop online banking portal for statement downloads.",
        "unique_features": [
            "Biometric security references (face or voice ID) may appear in statement authentication headers",
            "Fixed saver accounts show maturity dates and interest rates alongside transaction entries",
            "Instant Saver interest calculations are detailed with daily accrual breakdowns",
            "Mortgage account statements include detailed repayment schedules",
        ],
        "typical_users": "Appeals to UK savers seeking competitive fixed-rate savings and mortgage products through an app-only experience. Popular with rate-conscious customers comfortable with digital-only banking",
        "history_note": "Founded in 2014 in Durham, Atom Bank was the UK's first app-only bank to receive a full banking licence. Their focus on savings and mortgages (rather than current accounts) means their statements are primarily savings-focused with a clean, modern layout.",
    },
    "chase-uk": {
        "name": "Chase UK",
        "full_name": "Chase (J.P. Morgan UK)",
        "statement_notes": "Chase UK statements use a modern, minimal layout with merchant categories and cashback details. BankScan AI extracts all transaction data including rewards information.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "modern minimal layout with single Amount column, merchant category, and running balance",
        "common_issues": "Round-up savings transfers create micro-transactions that inflate row counts, cashback reward entries use a distinct format from regular credits, and the relatively new statement format lacks the backward compatibility issues of legacy banks",
        "digital_banking_tip": "In the Chase UK app, go to your account, tap 'Statements', and select the month to download as PDF. Chase UK is app-only, so all statement downloads are through the mobile app.",
        "unique_features": [
            "Cashback reward percentages and amounts are displayed alongside qualifying transactions",
            "Round-up savings transfers to linked savings accounts appear as paired micro-debits",
            "Merchant logos and categories are referenced in statement transaction descriptions",
            "Interest rate and cashback tier information appears in a summary header",
        ],
        "typical_users": "Popular with UK customers attracted by Chase's 1% cashback and competitive savings rates. Appeals to younger demographics familiar with digital-first banking who want J.P. Morgan's financial backing",
        "history_note": "Chase UK launched in 2021 as J.P. Morgan's first retail banking operation outside the United States. Their statement format was designed from scratch for mobile-first customers, making it one of the cleanest and most modern UK bank statement layouts.",
    },
    "wise": {
        "name": "Wise",
        "full_name": "Wise (formerly TransferWise)",
        "statement_notes": "Wise multi-currency statements include transactions in multiple currencies with exchange rates. BankScan AI preserves currency codes, rates, and fee breakdowns.",
        "formats": ["PDF", "CSV"],
        "popular": False,
        "date_format": "DD-MM-YYYY",
        "column_layout": "multi-currency layout with separate transaction lists per currency, each showing Amount, Fee, Exchange Rate, and Balance",
        "common_issues": "Multi-currency accounts generate separate sections per currency that can fragment during parsing, transfer fee breakdowns add extra rows between transactions, and the mid-market exchange rate details create additional text within each transfer entry",
        "digital_banking_tip": "In Wise, go to your account, click 'Statements', select the currency and date range, then choose PDF or CSV format. Each currency balance generates its own statement, so multi-currency users need to download statements per currency.",
        "unique_features": [
            "Each transfer shows the mid-market exchange rate used alongside the fee charged",
            "Multi-currency balances generate independent statement sections per currency",
            "Transfer fee breakdowns show the exact cost structure for each international payment",
            "Wise Business statements include team member spending attribution",
        ],
        "typical_users": "Popular with international freelancers, expats, and businesses making cross-border payments. Widely used by e-commerce sellers and remote workers who receive income in multiple currencies",
        "history_note": "Founded in 2011 as TransferWise and rebranded to Wise in 2021. Originally a peer-to-peer currency transfer service, Wise now offers multi-currency accounts with local bank details in multiple countries, making their statements uniquely complex with per-currency transaction lists.",
    },
    "paypal": {
        "name": "PayPal",
        "full_name": "PayPal UK",
        "statement_notes": "PayPal statements include a mix of payments, refunds, fees, and currency conversions. BankScan AI separates transaction types and preserves fee details.",
        "formats": ["PDF", "CSV"],
        "popular": False,
        "date_format": "DD/MM/YYYY HH:MM:SS",
        "column_layout": "multi-row layout where each transaction shows Gross, Fee, and Net amounts on separate lines with running balance",
        "common_issues": "Each transaction generates multiple rows (gross, fee, net) that must be consolidated, currency conversion entries create additional paired rows, and PayPal's internal transfers to linked bank accounts appear as separate withdrawal entries",
        "digital_banking_tip": "In PayPal, go to 'Activity', click 'Statements', select the month, and download as PDF. For CSV exports suitable for accounting software, use 'Activity' > 'Download' and select the date range and format. The CSV export includes more detail than the PDF.",
        "unique_features": [
            "Each transaction is broken into Gross, Fee, and Net sub-rows requiring consolidation",
            "Currency conversion entries show both source and destination amounts with the exchange rate",
            "PayPal buyer/seller protection hold entries appear as temporary balance adjustments",
            "Withdrawal entries to linked bank accounts carry the bank account last four digits",
        ],
        "typical_users": "Widely used by online sellers, freelancers, and small businesses for receiving payments. Common among eBay sellers, international freelancers, and anyone accepting online payments without a merchant account",
        "history_note": "PayPal has been operating since 1998 and became one of the first widely-adopted online payment systems. Their statement format is more complex than traditional banks because every transaction records gross, fee, and net amounts as separate line items.",
    },
    "stripe": {
        "name": "Stripe",
        "full_name": "Stripe Payments UK",
        "statement_notes": "Stripe payout statements list individual charges, refunds, and fees per payout batch. BankScan AI expands payout summaries into line-by-line transaction detail.",
        "formats": ["PDF", "CSV"],
        "popular": False,
        "date_format": "YYYY-MM-DD",
        "column_layout": "payout-centric layout listing individual charges, refunds, fees, and adjustments grouped by payout batch with net settlement amount",
        "common_issues": "Payouts bundle dozens or hundreds of individual charges into a single bank deposit making reconciliation complex, Stripe Connect platform fees add additional fee layers, and disputed charges appear as separate adjustment entries that must be matched to original transactions",
        "digital_banking_tip": "In the Stripe Dashboard, go to 'Balance' > 'Payouts', click on any payout to see its component transactions. For bulk exports, use 'Reports' > 'Financial reports' to download CSV files. The Stripe API also supports programmatic statement generation.",
        "unique_features": [
            "Each payout contains a detailed breakdown of all charges, refunds, and fees that compose the net settlement",
            "Stripe Connect marketplace fees and application fees are itemised separately",
            "Dispute and chargeback entries reference the original transaction ID for matching",
            "Multi-currency payouts show conversion details from settlement to payout currency",
        ],
        "typical_users": "The leading payment processor for SaaS companies, e-commerce businesses, and online platforms. Used by startups and enterprises alike for online payment acceptance",
        "history_note": "Founded in 2010, Stripe processes hundreds of billions of dollars annually. Their statement format is designed for developer-friendly reconciliation, with machine-readable payout reports that differ significantly from traditional bank statements.",
    },
    "amex": {
        "name": "American Express",
        "full_name": "American Express UK",
        "statement_notes": "American Express statements include card transactions, membership rewards, and payment summaries. BankScan AI extracts all transaction detail including merchant categories.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YY",
        "column_layout": "charge card layout with separate sections for new charges, payments, credits, and membership rewards summary",
        "common_issues": "Supplementary cardholder transactions are listed separately and must be consolidated, membership reward point entries are interspersed with financial transactions, and the multi-section layout (charges, payments, interest, fees) requires section-aware parsing",
        "digital_banking_tip": "In Amex Online, go to 'Statements & Activity', select the statement period, and download as PDF. For CSV exports, use 'Download your statement data' and choose the date range. The Amex app also offers statement downloads.",
        "unique_features": [
            "Membership Rewards points earned are displayed alongside qualifying transactions",
            "Supplementary cardholder transactions appear in a dedicated sub-section",
            "Annual fee and card renewal charges appear as separate line items",
            "Foreign currency transactions show both original currency and GBP amounts with the exchange rate and cross-border fee",
        ],
        "typical_users": "Popular with business professionals and frequent travellers who value premium rewards and travel benefits. Common among company expense card holders and high-spending consumers",
        "history_note": "American Express has operated in the UK since 1895. Unlike Visa and Mastercard, Amex operates as both the card network and issuer, giving their statements a unique format that includes rewards tracking not seen in standard bank-issued card statements.",
    },
    "credit-card": {
        "name": "Credit Card",
        "full_name": "UK Credit Card Statements",
        "statement_notes": "Credit card statements from any UK issuer — including Barclaycard, MBNA, Capital One, Tesco Bank, and John Lewis — are parsed by BankScan AI with high accuracy.",
        "formats": ["PDF", "scanned PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY (varies by issuer)",
        "column_layout": "typical credit card format with Transaction Date, Post Date, Description, and Amount columns plus summary sections for payments, interest, and minimum payment due",
        "common_issues": "Each issuer uses a slightly different column layout and date format, interest calculation sections add non-transaction rows, and balance transfer promotional rate entries use distinct formatting from regular purchases",
        "digital_banking_tip": "Most UK credit card issuers offer PDF statement downloads through their online banking portals or apps. Check 'Statements' or 'Documents' in your card issuer's app. For Barclaycard, MBNA, or Capital One, log in to their respective websites for PDF downloads.",
        "unique_features": [
            "Minimum payment calculations and payment due dates appear in a prominent summary box",
            "Interest charges are broken down by transaction type (purchases, balance transfers, cash advances)",
            "PPI refund credits may appear on older statements as separate line items",
            "Annual fee charges and promotional rate expiry notices appear alongside transactions",
        ],
        "typical_users": "Used by UK consumers across all demographics. Credit card statements are commonly needed by accountants and bookkeepers processing business expense cards, and by individuals during mortgage applications",
        "history_note": "UK credit card statements follow FCA-mandated disclosure requirements including persistent debt warnings and minimum payment calculations. The format has standardised somewhat since 2018 FCA reforms, but significant variation remains between issuers.",
    },
    "barclaycard": {
        "name": "Barclaycard",
        "full_name": "Barclaycard",
        "statement_notes": "Barclaycard statements list purchases, payments, interest charges, and rewards in a multi-section layout. BankScan AI extracts all transaction rows into a clean spreadsheet.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM",
        "column_layout": "multi-section credit card layout with separate areas for purchases, payments, cash advances, interest charges, and Avios/reward points",
        "common_issues": "The multi-section layout means transactions are split across different statement areas by type, Avios or cashback reward entries add non-financial rows, and the date format omits the year on individual transactions (shown only in the statement period header)",
        "digital_banking_tip": "In Barclaycard Online Servicing, click 'Statements', select the month, and download as PDF. For transaction data exports, use 'Download transactions' with CSV format. The Barclaycard app also offers statement downloads under 'More' > 'Statements'.",
        "unique_features": [
            "Avios points earned are displayed in a summary section with qualifying spend breakdown",
            "Payment protection insurance charges (if applicable) appear as separate monthly entries",
            "Promotional balance transfer rates and expiry dates are shown in a dedicated box",
            "Contactless transaction indicators are included alongside the merchant name",
        ],
        "typical_users": "One of the UK's largest credit card issuers, popular across demographics. Common among Avios collectors using Barclaycard Avios cards for travel rewards",
        "history_note": "Barclaycard launched in 1966 as the UK's first credit card. It remains one of the UK's largest card issuers and was the original UK Visa issuer. Their statement format has evolved significantly but maintains a distinctive multi-section layout.",
    },
    "john-lewis": {
        "name": "John Lewis Finance",
        "full_name": "John Lewis Partnership Card",
        "statement_notes": "John Lewis credit card statements include partnership reward points alongside transactions. BankScan AI extracts the financial data while preserving reward details.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD MMM YYYY",
        "column_layout": "credit card layout with Transaction Date, Description, Amount columns, plus Partnership Points summary section",
        "common_issues": "Partnership Card reward point entries appear as non-financial rows mixed with transactions, Waitrose and John Lewis purchases carry different merchant category codes, and the shopping protection benefit claims use a unique format",
        "digital_banking_tip": "In John Lewis Finance Online, click 'Statements', select the statement period, and download as PDF. The John Lewis Finance app also offers statement access under 'Account' > 'Statements'.",
        "unique_features": [
            "Partnership Points (spending rewards) earned at John Lewis and Waitrose are highlighted separately",
            "John Lewis and Waitrose purchases earn bonus points shown alongside each qualifying transaction",
            "Shopping protection and purchase cover benefit details appear in the statement footer",
            "Annual voucher redemption entries appear as credit transactions",
        ],
        "typical_users": "Popular with regular John Lewis and Waitrose shoppers who value Partnership Points. Common among middle-income UK households loyal to the John Lewis Partnership brand",
        "history_note": "The John Lewis Partnership Card is issued by HSBC but branded and managed through the John Lewis Partnership. The statement format blends HSBC's underlying structure with John Lewis branding and the Partnership Points rewards tracking.",
    },
    "tesco-bank": {
        "name": "Tesco Bank",
        "full_name": "Tesco Bank",
        "statement_notes": "Tesco Bank current account and credit card statements use a distinct layout with Clubcard points information. BankScan AI extracts all financial transaction data.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "standard credit card layout with Transaction Date, Description, and Amount columns, plus Clubcard Points summary",
        "common_issues": "Clubcard points entries add non-financial data rows, Tesco Pay+ transactions use abbreviated merchant descriptions, and the credit card statement format differs significantly from the current account format",
        "digital_banking_tip": "In Tesco Bank Online Banking, go to 'Statements', select the account and period, and download as PDF. For credit cards, statements are available monthly under 'Your statements'. Transaction CSV exports are available for current accounts.",
        "unique_features": [
            "Clubcard Points earned on spending are displayed in a summary section",
            "Tesco store purchases show the specific store location in the description",
            "Credit card Clubcard Plus subscription benefits are itemised",
            "Insurance product payments (car, home, pet) appear alongside banking transactions",
        ],
        "typical_users": "Popular with Tesco shoppers who collect Clubcard Points on banking transactions. Common among UK consumers who consolidate their financial products with a single supermarket banking brand",
        "history_note": "Tesco Bank was formed in 1997 as a joint venture with Royal Bank of Scotland before becoming wholly owned by Tesco in 2008. Following the sale of most banking operations to Barclays in 2024, the statement format may change for migrated customers.",
    },
    "n26": {
        "name": "N26",
        "full_name": "N26 (UK)",
        "statement_notes": "N26 digital bank statements use a modern format with IBAN references and categorised spending. BankScan AI handles the European-style date and amount formatting.",
        "formats": ["PDF", "CSV"],
        "popular": False,
        "date_format": "DD/MM/YYYY",
        "column_layout": "European digital bank format with IBAN references, single Amount column with +/- signs, and spending category tags",
        "common_issues": "European-style amount formatting with comma decimal separators may appear in some exports, IBAN-based references are longer than UK sort code formats, and Spaces (sub-accounts) transfers appear as internal transactions",
        "digital_banking_tip": "In the N26 app, go to 'My Account', tap 'Statements', select the period, and download as PDF or CSV. N26 also supports direct data exports to accounting tools through their API integrations.",
        "unique_features": [
            "IBAN and BIC references are used instead of UK sort codes and account numbers",
            "Spending categories with detailed merchant classification are included",
            "Spaces (sub-account) transfers appear as paired internal transactions",
            "Insurance partner product transactions carry distinctive reference codes",
        ],
        "typical_users": "Popular with European expats in the UK and digitally-native customers who prefer a pan-European banking app. Used by travellers and international workers needing a multi-country banking solution",
        "history_note": "N26 is a German digital bank that operated in the UK under a European passport until Brexit forced its UK withdrawal in 2022. Existing UK customer statements from before the withdrawal remain in client archives and still need processing.",
    },
    # --- US Banks ---
    "chase": {
        "name": "Chase",
        "full_name": "JPMorgan Chase Bank",
        "statement_notes": "Chase statements use a clean monthly format with separate sections for deposits, withdrawals, and checks. BankScan AI's US parser handles Chase's unique layout including direct deposits and ACH transfers.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "MM/DD/YYYY",
        "column_layout": "sectioned layout with separate areas for Deposits and Additions, Electronic Withdrawals, Checks Paid, and Daily Ending Balance",
        "common_issues": "Transactions are grouped by type rather than chronologically which complicates date-ordered reconciliation, Zelle transfer descriptions truncate recipient names, and Chase Sapphire reward redemptions appear as statement credits that can be confused with refunds",
        "digital_banking_tip": "In Chase Online, go to your account, click 'Statements & documents', select the month, and download as PDF. For transaction exports, click the download icon on your account activity page and choose CSV, OFX, or QFX format.",
        "unique_features": [
            "Transactions are grouped by type (deposits, electronic withdrawals, checks) rather than chronologically",
            "Daily Ending Balance section provides a day-by-day balance summary",
            "Chase QuickPay/Zelle transfers show the recipient email or phone number",
            "Overdraft protection transfers from linked savings are itemised separately",
        ],
        "typical_users": "The largest US bank by assets, serving one in six American households. Popular across all demographics from checking accounts to Chase Private Client wealth management",
        "history_note": "JPMorgan Chase was formed from the 2000 merger of J.P. Morgan & Co. and Chase Manhattan Corporation. Their statement format is one of the most commonly encountered by US accountants and is widely recognised as a benchmark for US bank statement layouts.",
    },
    "bank-of-america": {
        "name": "Bank of America",
        "full_name": "Bank of America",
        "statement_notes": "Bank of America statements include CHECKCARD entries, ACH transfers, and wire transactions in a multi-section layout. BankScan AI extracts all transaction types with full descriptions.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "MM/DD/YYYY",
        "column_layout": "multi-section layout with Deposits and Other Credits, Withdrawals and Other Debits, Checks, and Daily Balances in separate sections",
        "common_issues": "CHECKCARD prefixed entries truncate merchant names to fit the column width, ACH transfer descriptions include cryptic originator codes, and Keep the Change savings transfers create micro-transaction entries that inflate row counts",
        "digital_banking_tip": "In Bank of America Online Banking, go to 'Statements & Documents', select your account and the statement period, then click 'Download (PDF)'. For QFX or CSV exports, use 'Download transactions' from the account activity page.",
        "unique_features": [
            "CHECKCARD prefix is used for all debit card point-of-sale transactions",
            "Keep the Change round-up savings transfers appear as paired micro-transactions",
            "ACH deposits show the originating company ID alongside the transaction description",
            "Service charge summary section itemises monthly maintenance fees and ATM charges",
        ],
        "typical_users": "The second-largest US bank by assets, serving approximately 68 million consumer and small business clients. Particularly strong on the East Coast and in California",
        "history_note": "Bank of America traces its origins to the Bank of Italy founded in San Francisco in 1904. The current entity was formed through the merger with NationsBank in 1998. Their CHECKCARD transaction prefix is one of the most recognisable formatting conventions in US bank statements.",
    },
    "wells-fargo": {
        "name": "Wells Fargo",
        "full_name": "Wells Fargo Bank",
        "statement_notes": "Wells Fargo statements organize transactions by type with running daily balances. BankScan AI handles Wells Fargo's distinctive debit/credit column layout.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "MM/DD",
        "column_layout": "chronological transaction listing with separate Deposits, Withdrawals, and Checks sections, plus a Transaction Summary and Daily Balance summary",
        "common_issues": "The date format omits the year on individual transactions (only shown in the statement period header), PURCHASE and PURCHASE RETURN prefixes add length to descriptions, and Wells Fargo Advisors investment sweeps appear as banking transactions",
        "digital_banking_tip": "In Wells Fargo Online, go to 'Statements & Documents', select your account, choose the month, and download as PDF. For data exports, click the download arrow on the activity page and select QFX, CSV, or OFX format.",
        "unique_features": [
            "Transaction dates show only MM/DD without the year, which is in the statement header",
            "PURCHASE and PURCHASE RETURN prefixes identify point-of-sale debit card transactions",
            "Daily Balance summary table shows the ending balance for each day of the statement period",
            "Transaction Summary section provides totals for deposits, withdrawals, checks, and fees",
        ],
        "typical_users": "The third-largest US bank by assets, with particular strength in the Western US and mortgage lending. Serves approximately 70 million customers across retail, commercial, and wealth management",
        "history_note": "Wells Fargo was founded during the California Gold Rush in 1852. Their stagecoach logo remains one of the most recognised banking brands in the US. The 2016 fake accounts scandal led to regulatory changes that affected their statement and account reporting practices.",
    },
    "citibank": {
        "name": "Citibank",
        "full_name": "Citigroup / Citibank",
        "statement_notes": "Citibank statements include checking, savings, and credit card transactions with detailed merchant descriptions. BankScan AI parses all Citibank account types.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "MM/DD/YYYY",
        "column_layout": "combined statement layout with separate sections for checking, savings, and credit card accounts, each with their own column structures",
        "common_issues": "Combined statements covering multiple account types require section-aware parsing, Citigold and Citi Priority relationship summaries add non-transaction pages, and international Citibank transfers carry long reference codes that overflow description columns",
        "digital_banking_tip": "In Citi Online, go to 'Account Management' > 'Statements', select the account and period, and download as PDF. For QFX or CSV exports, use the 'Download' option on the account activity page. The Citi Mobile app also offers statement downloads.",
        "unique_features": [
            "Combined relationship statements show checking, savings, and credit card activity in one document",
            "Citigold and Citi Priority relationship pricing benefits are summarised in a header section",
            "International wire transfers include SWIFT codes and intermediary bank details",
            "ThankYou Points earned are displayed in a summary section on credit card statements",
        ],
        "typical_users": "Strong presence in major US metropolitan areas and among internationally-connected customers. Popular with expats and global professionals who use Citibank's international branch network across 90+ countries",
        "history_note": "Citibank traces its history to 1812 as City Bank of New York. It operates in more countries than any other US bank, which is reflected in their statement format's support for multi-currency and international transaction details.",
    },
    "us-bank": {
        "name": "US Bank",
        "full_name": "U.S. Bancorp",
        "statement_notes": "US Bank statements feature a traditional column layout with transaction codes and reference numbers. BankScan AI preserves all transaction detail during conversion.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "MM/DD",
        "column_layout": "traditional layout with Date, Description, Withdrawals, Deposits, and Daily Balance columns in a single chronological listing",
        "common_issues": "Transaction descriptions include lengthy ACH originator information that can wrap across multiple lines, Smartly checking perks create separate reward credit entries, and the compact PDF layout can cause column alignment issues when descriptions are long",
        "digital_banking_tip": "In US Bank Online Banking, go to 'Accounts', select your account, click 'Statements & Documents', and download the monthly PDF. For transaction data exports, use the 'Download' button on the transaction history page with OFX or CSV format.",
        "unique_features": [
            "ACH transaction descriptions include the full originating company identification",
            "US Bank Smartly checking account rewards appear as monthly credit entries",
            "Real-time payment alerts and notifications are referenced in transaction descriptions",
            "FlexPerks reward points earning is tracked in credit card statements",
        ],
        "typical_users": "The fifth-largest US bank by assets, with strong presence in the Midwest and West. Serves approximately 19 million customers and is a major commercial banking and payments processor",
        "history_note": "U.S. Bancorp was formed through the merger of Firstar Corporation and the original U.S. Bancorp in 2001. Their Midwest banking heritage is reflected in their traditional, straightforward statement format that prioritises clarity over visual design.",
    },
    "capital-one": {
        "name": "Capital One",
        "full_name": "Capital One Financial",
        "statement_notes": "Capital One checking and credit card statements use a modern digital layout. BankScan AI handles both account types, extracting rewards points and cashback details alongside transactions.",
        "formats": ["PDF"],
        "popular": True,
        "date_format": "MM/DD/YYYY",
        "column_layout": "clean modern layout with Transaction Date, Post Date, Description, and Amount columns for credit cards; chronological with Withdrawals and Deposits for checking",
        "common_issues": "Credit card statements show both Transaction Date and Post Date which can cause date confusion, Venture Miles and Savor cashback rewards appear as statement credits mixed with regular transactions, and the 360 Checking and Performance Savings accounts use different layouts from credit cards",
        "digital_banking_tip": "In Capital One Online Banking, click your account, go to 'Statements', select the month, and download as PDF. For CSV or QFX exports, use 'Download Transactions' from the account activity page. The Capital One app also supports statement downloads.",
        "unique_features": [
            "Credit card statements display both Transaction Date and Post Date for each entry",
            "Venture Miles or Savor cashback earnings are summarised in a rewards section",
            "360 Checking accounts show a clean chronological layout distinct from the credit card format",
            "Purchase Eraser travel redemptions appear as statement credits with the redeemed booking reference",
        ],
        "typical_users": "One of the largest US credit card issuers, popular with rewards-focused consumers. Capital One 360 checking and savings are favoured by digital-first customers seeking no-fee banking",
        "history_note": "Capital One was founded in 1994 as a credit card company and expanded into full-service banking with the acquisition of ING Direct (now Capital One 360) in 2012. Their credit card statement format is one of the most commonly processed in the US.",
    },
    "pnc": {
        "name": "PNC",
        "full_name": "PNC Financial Services",
        "statement_notes": "PNC Bank statements include Virtual Wallet categories and spending insights. BankScan AI extracts the core transaction data while filtering PNC's analytics sections.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "multi-section layout with separate Deposits, Electronic Payments, Checks, and ATM/Debit Card sections, plus Virtual Wallet spending analytics",
        "common_issues": "Virtual Wallet spending analytics pages add non-transaction content that interferes with parsing, the Spend/Reserve/Growth sub-account structure creates internal transfer entries, and PNC SmartAccess prepaid card statements use a different format from standard checking",
        "digital_banking_tip": "In PNC Online Banking, go to 'Customer Service' > 'Statements', select the account and period, and download as PDF. For data exports, use 'Download Activity' on the account overview page with OFX or CSV format.",
        "unique_features": [
            "Virtual Wallet includes spending analysis graphs and category breakdowns in the statement",
            "Spend, Reserve, and Growth sub-accounts show internal transfers between them",
            "Low Cash Mode overdraft protection details are itemised when triggered",
            "PNC alerts and notification preferences are referenced in the statement footer",
        ],
        "typical_users": "Seventh-largest US bank by assets, with strong presence in the Mid-Atlantic and Midwest regions. Popular with consumers using the Virtual Wallet budgeting features and with small businesses in the Eastern US",
        "history_note": "PNC stands for Pittsburgh National Corporation. The bank significantly expanded in 2021 by acquiring BBVA USA, which nearly doubled its branch network. Former BBVA customers may still have legacy-format statements in their archives.",
    },
    "td-bank-us": {
        "name": "TD Bank",
        "full_name": "TD Bank (US)",
        "statement_notes": "TD Bank US statements use a straightforward layout with daily balances and categorized transactions. BankScan AI handles both personal and business TD Bank statement formats.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "straightforward chronological layout with Date, Description, Withdrawals, Deposits, and Balance columns",
        "common_issues": "Extended banking hours transactions show timestamps outside normal business hours which can affect date assignment, TD Ameritrade sweep account transfers appear as regular banking transactions, and the green-branded PDF uses colour formatting that can interfere with greyscale scanning",
        "digital_banking_tip": "In TD Bank Online Banking, click your account, go to 'Statements & Documents', select the period, and download as PDF. For CSV or OFX exports, use the 'Download' option on the transaction history page.",
        "unique_features": [
            "Extended hours transactions (TD is known as 'America's Most Convenient Bank' with longer branch hours) show precise timestamps",
            "TD Ameritrade/Schwab investment sweep account transfers are listed with reference codes",
            "Penny checking account fee waivers are noted in the service charge section",
            "Cross-border CAD/USD transactions for customers near the Canadian border include exchange rate details",
        ],
        "typical_users": "Strong presence along the US East Coast from Maine to Florida. Popular with customers who value extended branch hours and weekend banking. TD Bank serves approximately 10 million US customers",
        "history_note": "TD Bank is the US subsidiary of Toronto-Dominion Bank, Canada's second-largest bank. It entered the US market through the acquisition of Banknorth in 2004 and Commerce Bancorp in 2008, which brought its signature extended-hours banking model.",
    },
    "truist": {
        "name": "Truist",
        "full_name": "Truist Financial (formerly BB&T / SunTrust)",
        "statement_notes": "Truist statements combine the legacy BB&T and SunTrust formats. BankScan AI recognizes both heritage layouts and extracts transactions accurately.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "sectioned layout with Deposits and Credits, Checks Paid, and Other Withdrawals in separate sections with running daily balance",
        "common_issues": "Legacy BB&T and SunTrust statement formats still appear for customers who have not yet been fully migrated, the merger created accounts with different numbering schemes, and Truist One checking reward entries use a unique format",
        "digital_banking_tip": "In Truist Online Banking, go to 'Statements & Documents', select the account and period, and download as PDF. For QFX or CSV exports, use 'Download Transactions' from the account activity page.",
        "unique_features": [
            "Legacy BB&T and SunTrust formatting may still appear depending on account migration status",
            "Truist One checking tier benefits are summarised in the statement header",
            "Light Stream lending payments (Truist's online lending platform) appear as ACH debits",
            "Wealth management relationship summaries may accompany banking statements for Private Wealth clients",
        ],
        "typical_users": "Sixth-largest US bank by assets, with dominant presence in the Southeastern US. Serves approximately 12 million consumer households across 15 states from Virginia to Texas",
        "history_note": "Truist was formed from the 2019 merger of BB&T (Branch Banking and Trust) and SunTrust Banks, two major Southeast US banks. The merger combined two different statement formats, and accountants may encounter both legacy formats alongside the new Truist layout.",
    },
    "ally-bank": {
        "name": "Ally Bank",
        "full_name": "Ally Financial",
        "statement_notes": "Ally Bank's online-only statements use a clean digital format with Zelle transfers and interest calculations. BankScan AI preserves interest and transfer details.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "clean digital format with Date, Description, Amount, and Balance columns in chronological order",
        "common_issues": "High-yield savings interest entries appear daily or monthly depending on the account type which can inflate transaction counts, Ally Invest sweep transfers between banking and brokerage create internal transaction pairs, and the buckets feature creates savings sub-account transfers",
        "digital_banking_tip": "In Ally Online Banking, click your account, go to 'Statements & Tax Documents', select the period, and download as PDF. For CSV exports, use the 'Export' button on the transaction history page.",
        "unique_features": [
            "High-yield savings interest calculations are detailed with APY and daily accrual information",
            "Savings buckets (sub-accounts for goals) transfers appear as internal transactions",
            "Ally Invest brokerage sweep transfers are listed with investment account references",
            "No monthly fee structure means no service charge section in statements",
        ],
        "typical_users": "Popular with rate-conscious savers seeking high-yield online savings and CDs. Appeals to digitally-native customers comfortable with online-only banking who prioritise competitive interest rates",
        "history_note": "Ally Bank was originally GMAC Bank, the banking arm of General Motors' financing division. It rebranded to Ally in 2010 and pivoted to direct-to-consumer online banking. Their clean, digital-first statement format reflects their absence of legacy branch banking systems.",
    },
    "discover-bank": {
        "name": "Discover",
        "full_name": "Discover Financial Services",
        "statement_notes": "Discover checking and credit card statements include cashback rewards and promotional balance details. BankScan AI extracts all financial transactions with reward tracking.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "credit card format with Transaction Date, Post Date, Description, and Amount columns, plus Cashback Bonus summary; checking uses chronological Date/Description/Amount/Balance",
        "common_issues": "Cashback Bonus category rotation means reward percentages vary by quarter and appear in different summary sections, Discover it Miles vs Cashback reward tracking uses different formatting, and promotional 0% APR balance transfer entries use a distinct layout from regular purchases",
        "digital_banking_tip": "In Discover Online Banking, go to 'Statements', select the account and period, and download as PDF. For CSV exports, click 'Download' on the transaction activity page. The Discover app also supports statement downloads.",
        "unique_features": [
            "Cashback Bonus earnings are broken down by the rotating 5% category and standard 1% category",
            "FICO score is displayed on credit card statements each month at no charge",
            "No annual fee structure means no fee line items on credit card statements",
            "Promotional APR balances are tracked separately from regular purchase balances",
        ],
        "typical_users": "Popular with US consumers seeking no-annual-fee cashback credit cards with rotating bonus categories. Discover checking accounts appeal to fee-conscious customers wanting cashback on debit purchases",
        "history_note": "Discover was launched by Sears in 1985 as a cashback credit card and was spun off as an independent company in 2007. They also operate the Discover payment network, which means their statements carry network-level transaction details not found in Visa/Mastercard-issued cards.",
    },
    "charles-schwab": {
        "name": "Charles Schwab",
        "full_name": "Charles Schwab Bank",
        "statement_notes": "Schwab brokerage and checking account statements include investment transactions alongside banking. BankScan AI separates and converts the banking transactions.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "combined statement with separate banking and brokerage sections; checking shows Date, Description, Withdrawals, Deposits, and Balance",
        "common_issues": "Combined brokerage and banking statements require section-aware parsing to separate investment from banking transactions, automatic investment sweep transfers between checking and brokerage create paired entries, and Schwab Intelligent Portfolios robo-advisor transactions appear as banking transfers",
        "digital_banking_tip": "In Schwab.com, go to 'Accounts' > 'Statements', select the account type and period, and download as PDF. For QFX exports suitable for accounting software, use the 'Export' option on the account history page.",
        "unique_features": [
            "Brokerage account activity and checking account transactions may appear in a combined statement",
            "Automatic investment sweep transfers between checking and brokerage are itemised",
            "Unlimited ATM fee rebates worldwide are tracked and credited monthly",
            "Schwab Bank High Yield Investor Checking shows no foreign transaction fees on international ATM withdrawals",
        ],
        "typical_users": "Popular with investors and traders who want seamless integration between brokerage and banking. Favoured by international travellers for unlimited ATM fee rebates and no foreign transaction fees",
        "history_note": "Charles Schwab pioneered discount brokerage in 1975 and added banking services in 2003. The 2020 acquisition of TD Ameritrade made Schwab the largest US brokerage. Their statements uniquely blend banking and investment data in ways that differ from traditional bank-only formats.",
    },
    "fifth-third": {
        "name": "Fifth Third",
        "full_name": "Fifth Third Bancorp",
        "statement_notes": "Fifth Third Bank statements use a traditional Midwest banking layout with check images and detailed ACH descriptions. BankScan AI handles the full transaction detail.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "traditional Midwest banking layout with separate sections for Deposits, Withdrawals, Checks, and Service Charges, plus daily balance listing",
        "common_issues": "Check image pages interspersed with transaction pages can disrupt table parsing, Momentum checking account reward entries add non-standard rows, and the traditional layout uses tighter column spacing than modern digital bank statements",
        "digital_banking_tip": "In Fifth Third Online Banking, go to 'Statements & Documents', select the account and period, and download as PDF. For CSV or OFX exports, use the 'Download' option on the transaction history page.",
        "unique_features": [
            "Check images may be included as separate pages within the statement PDF",
            "Momentum Banking rewards and relationship rate bonuses are itemised in a summary section",
            "Fifth Third securities and wealth management references appear for relationship clients",
            "Early Pay direct deposit acceleration is noted on qualifying payroll deposits",
        ],
        "typical_users": "Strong presence in the Midwest and Southeast US, particularly Ohio, Michigan, Indiana, Kentucky, and Florida. Popular with commercial banking clients and mid-market businesses",
        "history_note": "Fifth Third Bank gets its unusual name from the 1908 merger of Fifth National Bank and Third National Bank in Cincinnati. The name stuck, making it one of the most distinctively named banks in America. Their traditional statement format reflects their Midwest community banking heritage.",
    },
    "citizens-bank": {
        "name": "Citizens Bank",
        "full_name": "Citizens Financial Group",
        "statement_notes": "Citizens Bank statements include both personal and business formats with detailed fee breakdowns. BankScan AI extracts transactions while noting service charges separately.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "traditional layout with Date, Description, Debits, Credits, and Balance columns, plus a detailed Service Charge Summary section",
        "common_issues": "Detailed fee breakdown sections add non-transaction rows that can interfere with parsing, Citizens Access online savings uses a different format from branch-based accounts, and student loan payment references from Citizens lending carry long reference codes",
        "digital_banking_tip": "In Citizens Online Banking, go to 'Statements & Documents', select the account and statement period, and download as PDF. For OFX or CSV exports, use the 'Download' option on the account activity page.",
        "unique_features": [
            "Detailed Service Charge Summary breaks down each monthly fee with waiver eligibility information",
            "Citizens Access (online-only savings) statements have a cleaner format than branch account statements",
            "Student loan payment cross-references appear for customers with Citizens student lending",
            "Citizens Pay merchant financing payments (Apple, Microsoft, etc.) are listed as recurring debits",
        ],
        "typical_users": "Major presence in the Northeast and Mid-Atlantic US, particularly in New England and the Philadelphia area. Popular with retail customers, students, and mid-market commercial clients",
        "history_note": "Citizens Financial Group was a subsidiary of Royal Bank of Scotland until its IPO in 2014. Despite its UK parent company heritage, Citizens operates exclusively in the US with a statement format that follows American banking conventions.",
    },
    "regions-bank": {
        "name": "Regions Bank",
        "full_name": "Regions Financial Corporation",
        "statement_notes": "Regions Bank statements feature a Southern banking format with overdraft details and LifeGreen account features. BankScan AI parses all transaction types accurately.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "Southern regional banking format with separate sections for Deposits, Withdrawals, Checks, and ATM/Debit Card transactions, plus Account Analysis for business",
        "common_issues": "LifeGreen checking savings tier rewards create additional credit entries, overdraft protection transfer details add extra rows, and business Account Analysis statements have a completely different multi-page format from personal statements",
        "digital_banking_tip": "In Regions Online Banking, go to 'Accounts' > 'Statements & Documents', select the account and period, and download as PDF. For QFX or CSV exports, use 'Download Activity' from the account details page.",
        "unique_features": [
            "LifeGreen checking account tier benefits and savings rate bonuses are summarised in the header",
            "Overdraft protection transfer details from linked savings are itemised",
            "Now Banking prepaid card statements use a distinct format from traditional checking",
            "Business Account Analysis statements include detailed service charge calculations",
        ],
        "typical_users": "Strong presence across the Southeast US, particularly Alabama, Tennessee, Mississippi, and Georgia. Popular with community banking customers and commercial clients in the Southern US",
        "history_note": "Regions Financial is headquartered in Birmingham, Alabama, and was formed through the 2004 merger of Regions Financial and AmSouth Bancorporation. Their statement format reflects the community banking traditions of the Southeast US with a focus on clarity and service charge transparency.",
    },
    "keybank": {
        "name": "KeyBank",
        "full_name": "KeyCorp / KeyBank",
        "statement_notes": "KeyBank statements include both personal and commercial formats. BankScan AI handles KeyBank's multi-section layout with proper debit/credit classification.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "multi-section layout with Deposits, Other Credits, Checks Paid, and Other Debits sections, plus daily balance summary",
        "common_issues": "Key2More Rewards points entries add non-financial rows, the commercial banking statement format uses significantly wider columns and more detail than personal accounts, and Laurel Road student loan refinancing payments carry long reference strings",
        "digital_banking_tip": "In KeyBank Online Banking, go to 'Statements & Documents', select the account and period, and download as PDF. For OFX or CSV data exports, use the 'Download' option on the account activity page.",
        "unique_features": [
            "Key2More Rewards relationship points are tracked in a summary section",
            "Laurel Road (KeyBank's digital lending brand) loan payments appear as ACH debits with distinctive references",
            "Key Private Client wealth management summaries may accompany banking statements",
            "Commercial Account Analysis statements include detailed treasury management fee breakdowns",
        ],
        "typical_users": "Serves the Northeast and Midwest US with strong presence in Ohio, New York, and Washington state. Popular with mid-market commercial clients and healthcare and technology sector businesses",
        "history_note": "KeyCorp was formed from the 1994 merger of Society Corporation and KeyCorp (originally Commercial Bank of Albany, founded in 1825). The 2016 acquisition of First Niagara Financial Group expanded their footprint significantly across the Northeast.",
    },
    "huntington-bank": {
        "name": "Huntington Bank",
        "full_name": "Huntington Bancshares",
        "statement_notes": "Huntington Bank statements include 24-Hour Grace overdraft details and Asterisk-Free checking features. BankScan AI extracts core transactions from the unique layout.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "Midwest community banking layout with Date, Description, Subtractions, Additions, and Balance columns, plus 24-Hour Grace overdraft detail section",
        "common_issues": "24-Hour Grace overdraft protection details add unique status rows showing pending overdraft resolution, Asterisk-Free checking means no hidden fee entries but the fee waiver notices still appear, and TCF Bank legacy statements have a different format post-merger",
        "digital_banking_tip": "In Huntington Online Banking, go to 'Statements & Documents', select the account and period, and download as PDF. For OFX exports, use the 'Download Transactions' option on the account activity page.",
        "unique_features": [
            "24-Hour Grace overdraft protection details show the overdraft amount and the deadline to cover it",
            "Asterisk-Free checking transparency means no hidden fee entries in the service charge section",
            "Standby Cash interest-free overdraft line entries appear as separate credit/debit pairs",
            "Money Scout automatic savings transfer entries are itemised with round-up details",
        ],
        "typical_users": "Strong presence in the Midwest, particularly Ohio, Michigan, Indiana, and Wisconsin. Known for consumer-friendly overdraft policies that attract customers sensitive to bank fees",
        "history_note": "Huntington Bancshares was founded in Columbus, Ohio in 1866. The 2021 acquisition of TCF Financial (formerly Chemical Financial) made Huntington the largest bank headquartered in the Midwest. Former TCF customers may have legacy-format statements.",
    },
    "m-and-t-bank": {
        "name": "M&T Bank",
        "full_name": "M&T Bank Corporation",
        "statement_notes": "M&T Bank statements serve the Northeast and Mid-Atlantic region with a traditional layout. BankScan AI handles M&T's check and ACH transaction formatting.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "traditional Northeast banking layout with separate sections for Deposits, Checks, Electronic Transactions, and Service Charges",
        "common_issues": "People's United Bank legacy statements appear for recently merged accounts with different formatting, check number tracking in the Checks Paid section uses a compact format that can merge with descriptions, and commercial account analysis statements are significantly more complex than personal ones",
        "digital_banking_tip": "In M&T Online Banking, go to 'Statements & Notices', select the account and period, and download as PDF. For QFX or CSV exports, use the 'Download' button on the account activity page.",
        "unique_features": [
            "Check number tracking is prominently displayed in a dedicated Checks Paid section",
            "People's United Bank legacy account references may appear for recently migrated customers",
            "M&T Business Banking account analysis includes detailed treasury management metrics",
            "Wilmington Trust wealth management references appear for private banking clients",
        ],
        "typical_users": "Dominant in the Northeast and Mid-Atlantic region, particularly New York, Pennsylvania, Maryland, and Delaware. Strong in commercial real estate lending and mid-market business banking",
        "history_note": "M&T Bank was founded in Buffalo, New York in 1856 as Manufacturers and Traders Trust Company. The 2022 acquisition of People's United Financial significantly expanded their footprint into New England. Warren Buffett's Berkshire Hathaway is a major shareholder.",
    },
    "bmo-us": {
        "name": "BMO",
        "full_name": "BMO (Bank of Montreal US)",
        "statement_notes": "BMO US statements (formerly BMO Harris) use a format common to Canadian-heritage US banks. BankScan AI handles both personal and business BMO statement layouts.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "Canadian-heritage format with Date, Description, Debits, Credits, and Balance columns, plus account summary and fee analysis sections",
        "common_issues": "BMO Harris legacy branding may still appear on older statements, cross-border CAD/USD transactions for customers with Canadian connections carry exchange rate details, and the Bank of the West acquisition added another legacy statement format to the mix",
        "digital_banking_tip": "In BMO Digital Banking, go to 'Statements & Documents', select the account and period, and download as PDF. For OFX or CSV exports, use the 'Export' option on the transaction history page.",
        "unique_features": [
            "Canadian parent bank heritage is visible in statement design conventions",
            "Cross-border CAD/USD transaction support for customers with Canadian banking relationships",
            "BMO Harris and Bank of the West legacy branding may appear on older statements",
            "BMO Relationship Banking tier benefits are summarised in the statement header",
        ],
        "typical_users": "Strong presence in the Midwest (Illinois, Wisconsin, Indiana) and Western US following the Bank of the West acquisition. Popular with customers needing cross-border US-Canada banking capabilities",
        "history_note": "BMO's US operations evolved from Harris Bank (founded in Chicago in 1882) through BMO Harris Bank, and expanded significantly with the 2023 acquisition of Bank of the West from BNP Paribas. This created three legacy statement formats that accountants may encounter.",
    },
    "first-republic": {
        "name": "First Republic",
        "full_name": "First Republic Bank (now JPMorgan)",
        "statement_notes": "First Republic private banking statements include wealth management summaries alongside checking transactions. BankScan AI extracts the core banking data accurately.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "premium private banking layout with separate checking, savings, and investment summary sections in a single consolidated statement",
        "common_issues": "Post-JPMorgan acquisition statement formats may differ from original First Republic layouts, wealth management summary pages interspersed with banking transactions require section detection, and the premium PDF styling uses custom fonts",
        "digital_banking_tip": "First Republic accounts have been migrated to JPMorgan Chase following the 2023 acquisition. Access statements through Chase Online Banking under 'Statements & Documents'. Historic First Republic statements may be available in the archived documents section.",
        "unique_features": [
            "Consolidated statements included checking, savings, money market, and investment accounts in one document",
            "White-glove banking relationship manager contact details appeared on each statement",
            "Mortgage and HELOC payment details were included alongside deposit account transactions",
            "No ATM fee rebate credits were automatically applied and itemised monthly",
        ],
        "typical_users": "Served high-net-worth individuals, professionals, and tech industry executives, particularly in San Francisco, New York, and other major coastal markets. Known for personalised service and jumbo mortgage lending",
        "history_note": "First Republic Bank was seized by the FDIC and sold to JPMorgan Chase in May 2023, making it the second-largest bank failure in US history. Historic First Republic statements are still encountered by accountants processing prior-year records and tax returns.",
    },
    "svb": {
        "name": "SVB",
        "full_name": "Silicon Valley Bank (now First Citizens)",
        "statement_notes": "SVB statements are common among tech startups and VC-backed companies. BankScan AI handles SVB's wire-heavy transaction layout with full reference preservation.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "commercial banking format with detailed wire transfer sections showing Originator, Beneficiary, and Reference fields alongside standard Date/Description/Amount columns",
        "common_issues": "Wire transfer descriptions are extremely long with full originator and beneficiary details spanning multiple lines, VC funding round deposits appear as large single entries requiring manual annotation, and First Citizens post-acquisition statements use a different layout from original SVB format",
        "digital_banking_tip": "SVB accounts are now operated by First Citizens BancShares. Access statements through SVB Online Banking (still branded SVB). Go to 'Statements', select the account and period, and download as PDF. For CSV exports, use 'Transaction Download' on the account activity page.",
        "unique_features": [
            "Wire transfer entries include full Originator-to-Beneficiary information chains",
            "Venture capital funding round deposits often appear as single large wire credits",
            "FX transaction details show spot rate, forward rate, and settlement date information",
            "Global banking services include multi-currency account balance summaries",
        ],
        "typical_users": "The bank of choice for technology startups, venture-backed companies, and the innovation economy. Used by approximately half of all US venture-backed startups before its collapse",
        "history_note": "Silicon Valley Bank collapsed in March 2023 in the largest US bank failure since 2008. First Citizens BancShares acquired the deposits and loans. SVB statements from before and after the acquisition are commonly encountered by startup accountants and auditors.",
    },
    "sofi": {
        "name": "SoFi",
        "full_name": "SoFi Bank",
        "statement_notes": "SoFi's digital-first banking statements include Vaults, direct deposit details, and cashback tracking. BankScan AI extracts all transaction data from SoFi's modern format.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "modern digital banking format with Date, Description, Amount, and Balance columns, plus Vaults savings goal tracking",
        "common_issues": "Vaults (savings goals) transfers create internal transaction pairs that can inflate totals, SoFi Invest brokerage sweep transfers appear as banking transactions, and the high-yield APY interest entries may appear daily or monthly depending on account settings",
        "digital_banking_tip": "In the SoFi app or SoFi.com, go to 'Bank' > 'Statements', select the account and period, and download as PDF. SoFi also supports CSV exports through the transaction history page.",
        "unique_features": [
            "Vaults savings goal transfers are itemised with the vault name and target amount",
            "SoFi Invest and SoFi Relay cross-product references appear in transaction descriptions",
            "Direct deposit paycheck details show up to two days early with Early Paycheck feature",
            "High-yield checking and savings APY interest is detailed with rate and accrual information",
        ],
        "typical_users": "Popular with millennials and younger professionals seeking an all-in-one financial platform covering banking, investing, lending, and insurance. Common among student loan refinancing customers who expand to full banking",
        "history_note": "SoFi (Social Finance) was founded in 2011 as a student loan refinancing company and obtained its bank charter in 2022 through the acquisition of Golden Pacific Bancorp. Their statement format reflects their fintech origins with a clean, modern design.",
    },
    "chime": {
        "name": "Chime",
        "full_name": "Chime Financial",
        "statement_notes": "Chime statements from their Bancorp/Stride Bank partnership use a clean mobile-first format. BankScan AI handles Chime's direct deposit and SpotMe transaction types.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "mobile-first format with Date, Description, Amount, and Balance columns, issued by The Bancorp Bank or Stride Bank as the underlying institution",
        "common_issues": "Statements are technically issued by The Bancorp Bank or Stride Bank (not Chime, which is not a bank), SpotMe overdraft coverage entries use a unique format, and the Save When You Get Paid automatic transfers create additional transaction entries",
        "digital_banking_tip": "In the Chime app, go to 'Settings' > 'Statements' or tap your account and select 'Monthly Statements'. Statements are only available through the mobile app. For historic statements, scroll through the available months.",
        "unique_features": [
            "Statement issuer is The Bancorp Bank or Stride Bank, not Chime (which is a financial technology company)",
            "SpotMe overdraft allowance usage and repayment entries carry distinctive descriptions",
            "Save When You Get Paid and Round Ups automatic savings transfers are itemised",
            "Pay Friends peer-to-peer payment entries show the recipient name",
        ],
        "typical_users": "Popular with US consumers who are underserved by traditional banks, particularly those seeking no-fee banking, early paycheck access, and fee-free overdraft coverage. Common among gig economy workers and hourly employees",
        "history_note": "Chime was founded in 2013 as a financial technology company and is not technically a bank. Banking services are provided through partner banks (The Bancorp Bank and Stride Bank). This distinction appears on every statement and affects how accountants categorise the institution.",
    },
    "marcus": {
        "name": "Marcus",
        "full_name": "Marcus by Goldman Sachs",
        "statement_notes": "Marcus savings and checking statements include APY calculations and interest details. BankScan AI extracts transaction data and interest accrual information.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "savings-focused format with Date, Description, Amount, and Balance columns, plus detailed APY and interest accrual summary",
        "common_issues": "Interest accrual detail rows can be confused with regular transactions, the savings-only focus means most transactions are transfers in/out rather than purchases, and Goldman Sachs Bank USA appears as the legal entity name which differs from the Marcus consumer brand",
        "digital_banking_tip": "In Marcus.com or the Marcus app, go to 'Statements', select the account and period, and download as PDF. Marcus focuses on savings products, so statements are relatively simple with primarily transfer and interest entries.",
        "unique_features": [
            "APY and interest rate details are prominently displayed with daily accrual calculations",
            "Goldman Sachs Bank USA appears as the legal entity on statement headers",
            "No-fee structure means no service charge section appears on statements",
            "CD maturity date and renewal terms are shown for certificate of deposit accounts",
        ],
        "typical_users": "Popular with US savers seeking competitive high-yield savings rates from a trusted brand. Appeals to consumers who want Goldman Sachs-level financial infrastructure without the private banking minimum requirements",
        "history_note": "Marcus was launched by Goldman Sachs in 2016, marking the 147-year-old investment bank's first foray into consumer banking. Named after Marcus Goldman, the firm's founder, the platform focuses on simple savings and lending products with a deliberately streamlined statement format.",
    },
    "usaa": {
        "name": "USAA",
        "full_name": "USAA Federal Savings Bank",
        "statement_notes": "USAA statements serve military families with a comprehensive layout including insurance and investment references. BankScan AI focuses on the banking transaction data.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "comprehensive military banking layout with separate sections for Deposits, Withdrawals, and Checks, plus cross-references to insurance and investment products",
        "common_issues": "Insurance premium payments and investment product references appear alongside banking transactions creating cross-product noise, military allotment deposits carry lengthy DFAS reference codes, and the comprehensive statement covering multiple products requires careful section filtering",
        "digital_banking_tip": "In USAA.com or the USAA app, go to 'Documents & Statements', select the banking account and period, and download as PDF. For CSV exports, use 'Download Transactions' from the account activity page. USAA also offers QFX format for accounting software.",
        "unique_features": [
            "Military pay allotment deposits from DFAS carry distinctive reference codes with service branch identifiers",
            "Insurance premium auto-pay debits for USAA auto, home, and life insurance appear alongside banking transactions",
            "Deployment-related account protections under SCRA are noted when applicable",
            "USAA investment account sweep transfers are cross-referenced in banking statements",
        ],
        "typical_users": "Exclusively serves US military members, veterans, and their families. USAA has approximately 13 million members and consistently ranks highest in customer satisfaction among US banks",
        "history_note": "USAA was founded in 1922 by 25 Army officers in San Antonio, Texas. Membership is limited to US military personnel and their families. Their statement format reflects the unique financial needs of military life, including deployment considerations and military pay structures.",
    },
    "navy-federal": {
        "name": "Navy Federal",
        "full_name": "Navy Federal Credit Union",
        "statement_notes": "Navy Federal Credit Union statements include share accounts, checking, and loan payments in a single statement. BankScan AI separates and converts the checking transactions.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "credit union format with combined Share Savings, Share Draft (checking), and loan account sections, each with Date, Description, Amount, and Balance columns",
        "common_issues": "Credit union terminology differs from standard banking (share accounts instead of savings, share draft instead of checking), combined statements covering savings, checking, and loan accounts require section separation, and dividend entries (interest) use credit union-specific language",
        "digital_banking_tip": "In Navy Federal Online Banking or the NFCU app, go to 'Statements & Documents', select the account and period, and download as PDF. For OFX or CSV exports, use the 'Download Activity' option on the account history page.",
        "unique_features": [
            "Credit union terminology uses 'share account' for savings and 'share draft' for checking",
            "Dividends (interest) are credited using credit union-specific language and calculations",
            "Combined statements show all member accounts including savings, checking, CDs, and loans",
            "Military pay allotments from DFAS carry service-specific reference codes similar to USAA",
        ],
        "typical_users": "The world's largest credit union by assets, serving over 13 million members. Open to all Department of Defense personnel, veterans, and their families. Popular for competitive loan rates and no-fee banking",
        "history_note": "Navy Federal Credit Union was founded in 1933 and originally served only Navy personnel. Membership has expanded to all DoD branches and their families. As a credit union, their statements use different terminology and structure from commercial banks, which can surprise accountants accustomed to standard banking formats.",
    },
    "comerica": {
        "name": "Comerica",
        "full_name": "Comerica Bank",
        "statement_notes": "Comerica commercial and personal banking statements include detailed ACH and wire transfer descriptions. BankScan AI preserves the full transaction reference data.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "commercial banking-oriented layout with detailed wire and ACH sections showing full originator/beneficiary information alongside standard Date/Description/Amount columns",
        "common_issues": "Commercial Account Analysis statements are multi-page documents with complex fee calculations, wire transfer descriptions include full SWIFT routing details that span multiple lines, and the treasury management reporting format differs significantly from personal banking statements",
        "digital_banking_tip": "In Comerica Web Banking, go to 'Statements', select the account and period, and download as PDF. For business accounts, the Comerica Business Connect portal offers enhanced reporting and CSV/BAI2 export formats for treasury management.",
        "unique_features": [
            "Wire transfer entries include complete SWIFT routing chain with intermediary bank details",
            "Commercial Account Analysis provides detailed earnings credit rate and fee offset calculations",
            "Treasury management services including lockbox and positive pay entries carry distinctive codes",
            "Business checking statements include detailed ACH originator identification",
        ],
        "typical_users": "Strong in Michigan, Texas, and California with a focus on commercial banking, particularly technology and life sciences, energy, and real estate sectors. Known for treasury management and commercial lending",
        "history_note": "Comerica was founded in Detroit in 1849 as the Detroit Savings Fund Institute and relocated its headquarters to Dallas in 2007. Despite the move, it remains one of the largest banks headquartered in the Southwest and maintains strong Michigan roots.",
    },
    "zions-bank": {
        "name": "Zions Bank",
        "full_name": "Zions Bancorporation",
        "statement_notes": "Zions Bank statements serve the Western US with a traditional regional banking format. BankScan AI handles the layout including business account analysis sections.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "traditional Western US regional banking format with Date, Description, Debits, Credits, and Balance columns, plus business account analysis for commercial clients",
        "common_issues": "Multiple subsidiary bank brands under Zions Bancorporation use slightly different statement formats, business account analysis sections add complex fee calculation pages, and the traditional layout can have tight column spacing on accounts with long ACH descriptions",
        "digital_banking_tip": "In Zions Bank Online Banking, go to 'Statements & Documents', select the account and period, and download as PDF. For CSV or OFX exports, use the 'Download' option on the account transaction page.",
        "unique_features": [
            "Zions Bancorporation operates multiple bank brands, and statements may carry different subsidiary branding",
            "Business Account Analysis includes detailed treasury management pricing and activity charges",
            "SBA lending payment entries carry Small Business Administration reference numbers",
            "Western US agricultural lending references are common in rural market account statements",
        ],
        "typical_users": "Dominant in Utah and strong across the Western US including Idaho, Wyoming, and Arizona. Popular with small businesses, agricultural operations, and commercial real estate clients in the Intermountain West",
        "history_note": "Zions Bancorporation was founded in 1873 in Salt Lake City and is closely associated with the economic development of the Intermountain West. It operates through several subsidiary bank brands in different states, each with slightly different statement formatting.",
    },
    "webster-bank": {
        "name": "Webster Bank",
        "full_name": "Webster Financial Corporation",
        "statement_notes": "Webster Bank statements include HSA accounts and commercial banking features common in the Northeast. BankScan AI extracts transactions from all Webster account types.",
        "formats": ["PDF"],
        "popular": False,
        "date_format": "MM/DD",
        "column_layout": "Northeast regional banking format with Date, Description, Withdrawals, Deposits, and Balance columns, plus HSA account-specific sections for healthcare accounts",
        "common_issues": "HSA Bank statements (Webster's Health Savings Account subsidiary) use a different format from standard banking statements, Sterling National Bank legacy statements may appear for recently merged accounts, and commercial banking account analysis adds multi-page fee calculations",
        "digital_banking_tip": "In Webster Online Banking, go to 'Statements & Documents', select the account and period, and download as PDF. For HSA accounts, use the HSA Bank portal separately at hsabank.com for statement downloads.",
        "unique_features": [
            "HSA Bank (Webster subsidiary) statements include healthcare expense categorisation and IRS contribution limit tracking",
            "Sterling National Bank legacy branding may appear on statements from recently merged accounts",
            "Commercial banking statements include equipment finance and asset-based lending details",
            "Private banking relationship summaries accompany statements for high-net-worth clients",
        ],
        "typical_users": "Serves the Northeast US, particularly Connecticut and the New York metropolitan area. Webster is one of the largest HSA administrators in the US through its HSA Bank subsidiary, serving millions of health savings account holders nationwide",
        "history_note": "Webster Bank was founded in Waterbury, Connecticut in 1935. The 2022 merger with Sterling Bancorp significantly expanded their New York presence. Their HSA Bank subsidiary is one of the top three HSA custodians in the US, making their healthcare-focused statement format uniquely important for benefits administrators.",
    },
    "mercury": {
        "name": "Mercury",
        "full_name": "Mercury (startup banking)",
        "statement_notes": "Mercury's fintech banking statements are popular with startups and tech companies. BankScan AI handles Mercury's wire-heavy, API-friendly transaction format.",
        "formats": ["PDF", "CSV"],
        "popular": False,
        "date_format": "MM/DD/YYYY",
        "column_layout": "modern fintech format with Date, Description, Amount, and Balance columns, plus detailed wire transfer and ACH originator information",
        "common_issues": "Statements are issued by Choice Financial Group or Evolve Bank & Trust (Mercury's partner banks) rather than Mercury itself, wire transfer descriptions can be very long with full beneficiary details, and team spending card transactions carry the team member name as a prefix",
        "digital_banking_tip": "In Mercury's dashboard, go to 'Accounts', select the account, click 'Statements', and download as PDF. For CSV exports, use the 'Export' button on the transactions page. Mercury also offers API access for programmatic statement generation.",
        "unique_features": [
            "Statement issuer is Choice Financial Group or Evolve Bank & Trust, not Mercury (which is a fintech company)",
            "Team spending card transactions include the cardholder name as part of the description",
            "Treasury account interest calculations show the yield rate and daily accrual",
            "API-generated transaction exports include metadata fields not present in the PDF statement",
        ],
        "typical_users": "The leading banking platform for startups and technology companies, particularly venture-backed companies. Popular with YCombinator and other accelerator alumni. Serves over 200,000 startups",
        "history_note": "Mercury was founded in 2019 and quickly became the default banking choice for US startups. Like Chime, Mercury is not a bank itself but a financial technology company partnering with FDIC-insured banks. Their clean, modern statement format is designed for integration with startup accounting workflows.",
    },
}

# ---------------------------------------------------------------------------
# Professions / verticals that need bank statement or receipt conversion
# ---------------------------------------------------------------------------
PROFESSIONS = {
    "accountants": {
        "name": "Accountants",
        "singular": "Accountant",
        "pain_point": "Accountants spend hours manually entering client bank statements into bookkeeping software. With hundreds of transactions per client per month, this is the single biggest time drain in practice.",
        "benefit": "BankScan AI lets you drag-and-drop any client's bank statement PDF and get a formatted Excel spreadsheet in seconds — ready to import into Xero, QuickBooks, or Sage.",
        "keywords": ["accountant bank statement tool", "accounting PDF converter", "accountant statement parser"],
        "combo_eligible": True,
        "workflow_detail": "Accountants typically receive client bank statements monthly, reconcile them against purchase and sales ledgers, post adjusting entries, and prepare management accounts. During year-end, they batch-process 12 months of statements for annual accounts preparation and corporation tax filings.",
        "compliance_requirements": "Accountants must comply with anti-money laundering (AML) regulations requiring them to verify client bank transactions. HMRC Making Tax Digital mandates quarterly digital submissions, and ICAEW/ACCA practice standards require accurate record-keeping with full audit trails.",
        "software_preferences": ["Xero", "Sage", "QuickBooks"],
        "time_savings": "Saves an average of 45 minutes per client per month on bank reconciliation, or 6+ hours per week for a typical practice with 30 clients",
        "peak_periods": "January-April for self-assessment deadline, plus month-end for management accounts clients and January/April/July/October for quarterly VAT returns",
        "industry_jargon": ["nominal ledger", "bank reconciliation", "trial balance", "management accounts", "year-end adjustments"],
        "unique_challenges": [
            "Handling statements from dozens of different banks with inconsistent PDF formats across a diverse client portfolio",
            "Matching bank transactions to purchase and sales invoices when descriptions are truncated or abbreviated by the bank",
            "Processing historic statements for new client onboarding where years of backlog need digitising quickly"
        ],
    },
    "bookkeepers": {
        "name": "Bookkeepers",
        "singular": "Bookkeeper",
        "pain_point": "Bookkeepers deal with statements from dozens of different banks, each with its own PDF format. Copy-pasting transactions is error-prone and tedious.",
        "benefit": "BankScan AI handles every major UK bank format automatically. Upload the PDF, download the spreadsheet — no manual data entry needed.",
        "keywords": ["bookkeeper bank statement converter", "bookkeeping PDF tool", "bookkeeper statement to Excel"],
        "combo_eligible": True,
        "workflow_detail": "Bookkeepers collect bank statements from clients weekly or monthly, categorise each transaction against the chart of accounts, reconcile balances, and flag discrepancies. They typically process statements in batches by client, posting entries into cloud accounting software before month-end close.",
        "compliance_requirements": "Bookkeepers operating under ICB or IAB membership must follow professional standards for data handling and client confidentiality. GDPR applies to all client financial data, and Making Tax Digital requires digital record-keeping for VAT-registered clients.",
        "software_preferences": ["Xero", "QuickBooks Online", "FreeAgent"],
        "time_savings": "Saves approximately 30 minutes per client per month on data entry, freeing up 8-10 hours per week for a bookkeeper with 20+ clients",
        "peak_periods": "Month-end for all clients, plus quarterly VAT deadlines and January for self-assessment clients needing full-year reconciliation",
        "industry_jargon": ["chart of accounts", "bank feed", "transaction categorisation", "month-end close", "unreconciled items"],
        "unique_challenges": [
            "Managing multiple client logins and bank feeds that frequently disconnect, forcing manual statement uploads",
            "Categorising ambiguous transactions when clients provide minimal context about their business expenses",
            "Reconciling opening balances when taking over books from a previous bookkeeper with incomplete records"
        ],
    },
    "solicitors": {
        "name": "Solicitors",
        "singular": "Solicitor",
        "pain_point": "Solicitors reviewing financial disclosure in divorce, fraud, or commercial cases need to analyse bank statements quickly and accurately.",
        "benefit": "Convert client and third-party bank statements to searchable, sortable Excel spreadsheets for faster case analysis and evidence preparation.",
        "keywords": ["solicitor bank statement tool", "legal statement converter", "financial disclosure tool"],
        "combo_eligible": True,
        "workflow_detail": "Solicitors receive bank statements as part of financial disclosure in family, fraud, or commercial litigation. They review statements to trace assets, verify income claims, identify suspicious transactions, and prepare schedules for counsel. Statements are often cross-referenced against Form E declarations in family proceedings.",
        "compliance_requirements": "SRA Accounts Rules require solicitors to reconcile client account bank statements within 5 weeks of the statement date. Failure to comply can result in regulatory action including fines or intervention. Anti-money laundering checks under the Proceeds of Crime Act 2002 also require scrutiny of client bank transactions.",
        "software_preferences": ["Clio", "LEAP Legal Software", "PracticeEvolve"],
        "time_savings": "Saves 1-2 hours per case on financial disclosure analysis, with complex matrimonial cases saving up to 4 hours per set of statements",
        "peak_periods": "Court filing deadlines drive peaks throughout the year, with particular pressure around Form E disclosure deadlines in family cases and trial preparation periods",
        "industry_jargon": ["financial disclosure", "Form E", "asset tracing", "client account reconciliation", "Scott schedule"],
        "unique_challenges": [
            "Analysing opposing party statements where transactions may be deliberately obscured or accounts spread across multiple institutions",
            "Handling redacted or incomplete statements where the court has ordered limited disclosure",
            "Maintaining chain of custody and evidential integrity when converting statements for use in court proceedings"
        ],
    },
    "small-business-owners": {
        "name": "Small Business Owners",
        "singular": "Small Business Owner",
        "pain_point": "Small business owners often need to reconcile bank statements with invoices and expenses but lack accounting software that imports PDFs directly.",
        "benefit": "Upload your bank statement PDF and get a clean spreadsheet you can use to track cash flow, reconcile invoices, or send to your accountant.",
        "keywords": ["small business statement converter", "SME bank statement tool", "business PDF to Excel"],
        "combo_eligible": True,
        "workflow_detail": "Small business owners typically download or receive monthly bank statements, forward them to their accountant or bookkeeper, and occasionally review them to check cash flow. Many manually compare statements against their sales invoices and expense receipts, often using spreadsheets rather than dedicated accounting software.",
        "compliance_requirements": "UK small businesses must keep financial records for at least 6 years for HMRC purposes. VAT-registered businesses must maintain digital records under Making Tax Digital and submit quarterly returns. Companies House requires annual accounts preparation based on accurate bank records.",
        "software_preferences": ["Xero", "QuickBooks", "Excel"],
        "time_savings": "Saves 1-2 hours per month on manual statement review and reconciliation, plus avoids errors that can cost hours to trace and correct",
        "peak_periods": "Year-end for annual accounts preparation, quarterly VAT return deadlines, and January for self-assessment if operating as a sole trader",
        "industry_jargon": ["cash flow", "profit and loss", "VAT return", "bank reconciliation", "management accounts"],
        "unique_challenges": [
            "Mixing personal and business transactions on the same bank account, requiring manual separation for tax purposes",
            "Lacking accounting knowledge to properly categorise transactions or understand what their accountant needs",
            "Managing cash flow gaps where statement data is needed urgently but the accountant has a backlog"
        ],
    },
    "tax-advisors": {
        "name": "Tax Advisors",
        "singular": "Tax Advisor",
        "pain_point": "Tax advisors need to review multiple years of bank statements during tax investigations or self-assessment preparation.",
        "benefit": "Batch-convert entire folders of bank statement PDFs into structured spreadsheets to speed up tax return preparation and HMRC enquiry responses.",
        "keywords": ["tax advisor statement tool", "HMRC statement converter", "tax return bank statement"],
        "combo_eligible": True,
        "workflow_detail": "Tax advisors collect bank statements alongside P60s, dividend vouchers, and rental income records to prepare self-assessment returns. During HMRC enquiries, they must review multiple years of statements to substantiate income, verify expense claims, and respond to information notices within tight deadlines.",
        "compliance_requirements": "Tax advisors must comply with HMRC's Agent Authorisation framework and Professional Conduct in Relation to Taxation (PCRT) guidelines. They have a duty to submit accurate returns and must retain supporting documentation including bank statements for the statutory record-keeping period.",
        "software_preferences": ["TaxCalc", "Taxfiler", "IRIS"],
        "time_savings": "Saves 2-3 hours per complex self-assessment return during tax season, and up to 8 hours when responding to HMRC enquiries requiring multi-year statement analysis",
        "peak_periods": "January-April for self-assessment deadline (31 January), October-December for early filing incentives, and ad-hoc peaks when HMRC opens enquiries or investigations",
        "industry_jargon": ["self-assessment", "HMRC enquiry", "information notice", "tax computation", "capital allowances"],
        "unique_challenges": [
            "Processing multiple years of historic bank statements for HMRC enquiries where clients have lost or never kept digital copies",
            "Cross-referencing bank transactions against multiple income sources such as rental properties, dividends, and self-employment to identify undeclared income",
            "Handling overseas bank statements in different currencies and formats for clients with international tax obligations"
        ],
    },
    "mortgage-brokers": {
        "name": "Mortgage Brokers",
        "singular": "Mortgage Broker",
        "pain_point": "Mortgage brokers need to verify income and spending from bank statements when assessing affordability for lender applications.",
        "benefit": "Instantly convert applicant bank statements to Excel to verify income, identify regular commitments, and prepare affordability summaries.",
        "keywords": ["mortgage broker statement tool", "affordability check tool", "mortgage statement converter"],
        "combo_eligible": True,
        "workflow_detail": "Mortgage brokers request 3-6 months of bank statements from applicants, review them for regular income deposits, identify committed expenditure such as loans and subscriptions, flag gambling transactions or payday loans, and prepare affordability summaries for lender submission. Each application typically involves reviewing statements from multiple accounts.",
        "compliance_requirements": "FCA regulations require mortgage brokers to conduct thorough affordability assessments under MCOB rules. They must verify income sources against bank statements, document their analysis, and retain records for at least 3 years. Anti-money laundering checks require scrutiny of the source of deposit funds.",
        "software_preferences": ["Mortgage Brain", "Twenty7Tec", "Excel"],
        "time_savings": "Saves 30-45 minutes per mortgage application on statement review, which adds up to 5+ hours per week during busy periods with 8-10 applications in progress",
        "peak_periods": "Spring and autumn housing market peaks (March-June and September-November), plus stamp duty deadline rushes and Help to Buy scheme deadlines",
        "industry_jargon": ["affordability assessment", "committed expenditure", "income verification", "debt-to-income ratio", "source of deposit"],
        "unique_challenges": [
            "Identifying and flagging gambling transactions, payday loans, or returned direct debits that lenders will scrutinise",
            "Verifying self-employed income from bank statements when applicants lack traditional payslips or P60s",
            "Processing statements from multiple accounts per applicant across different banks, each in different PDF formats"
        ],
    },
    "forensic-accountants": {
        "name": "Forensic Accountants",
        "singular": "Forensic Accountant",
        "pain_point": "Forensic accountants investigating fraud or financial irregularities need to process hundreds of bank statements into analysable data.",
        "benefit": "Bulk-convert bank statements into structured spreadsheets for pattern analysis, timeline reconstruction, and expert witness reporting.",
        "keywords": ["forensic accounting tool", "fraud investigation statement converter", "financial investigation software"],
        "combo_eligible": True,
        "workflow_detail": "Forensic accountants receive large volumes of bank statements through court orders or client disclosure. They convert statements to structured data, build transaction timelines, identify unusual patterns such as round-sum transfers or structuring, cross-reference transactions across multiple accounts, and prepare expert witness reports with supporting schedules.",
        "compliance_requirements": "Forensic accountants must follow evidence handling procedures that maintain chain of custody for court admissibility. They are bound by CPD requirements under ICAEW, ACCA, or CIMA, and expert witness reports must comply with Civil Procedure Rules Part 35 or Criminal Procedure Rules Part 19.",
        "software_preferences": ["CaseWare", "IDEA Data Analysis", "Excel with Power Query"],
        "time_savings": "Saves 4-8 hours per investigation on initial data extraction, allowing forensic accountants to focus on analysis rather than data entry across cases spanning hundreds of statements",
        "peak_periods": "Court-driven deadlines create unpredictable peaks, but fraud investigations often intensify during financial year-ends and following whistleblower reports or regulatory referrals",
        "industry_jargon": ["transaction tracing", "fund flow analysis", "structuring", "Benford's Law analysis", "expert witness report"],
        "unique_challenges": [
            "Processing statements from closed or defunct banks where PDF formats are outdated or non-standard",
            "Maintaining evidential integrity and chain of custody when converting bank statements for use in criminal or civil proceedings",
            "Cross-referencing thousands of transactions across multiple accounts belonging to different entities to identify connected payments"
        ],
    },
    "landlords": {
        "name": "Landlords",
        "singular": "Landlord",
        "pain_point": "Landlords managing multiple properties need to track rental income and expenses from bank statements for tax returns.",
        "benefit": "Convert your bank statements to Excel to easily categorise rental income, maintenance costs, and mortgage payments for self-assessment.",
        "keywords": ["landlord bank statement tool", "rental income tracker", "property expense converter"],
        "combo_eligible": True,
        "workflow_detail": "Landlords review bank statements to confirm tenant rent payments have been received, match maintenance invoices against bank debits, track mortgage interest payments for tax relief calculations, and collate all property-related transactions for their self-assessment tax return or accountant.",
        "compliance_requirements": "Landlords must report rental income on their self-assessment tax return and can only claim mortgage interest as a basic rate tax credit since Section 24 restrictions. Records must be kept for 5 years after the 31 January submission deadline. Deposit protection scheme compliance also requires clear financial records.",
        "software_preferences": ["Excel", "Hammock", "Landlord Vision"],
        "time_savings": "Saves 1-2 hours per property per month on income and expense tracking, with portfolio landlords saving a full day each month across 10+ properties",
        "peak_periods": "January for self-assessment tax return deadline, plus quarterly if providing management accounts to property management companies or mortgage lenders",
        "industry_jargon": ["rental yield", "Section 24", "allowable expenses", "wear and tear allowance", "capital gains"],
        "unique_challenges": [
            "Separating property-related transactions from personal spending when using the same bank account for both",
            "Tracking income and expenses across multiple properties held in different bank accounts or through letting agents",
            "Calculating mortgage interest tax relief correctly under Section 24 restrictions using bank statement data"
        ],
    },
    "charities": {
        "name": "Charities & Non-Profits",
        "singular": "Charity",
        "pain_point": "Charities need accurate financial records for trustees, donors, and the Charity Commission — but often rely on volunteers without accounting expertise.",
        "benefit": "Make bank statement reconciliation simple for volunteer treasurers. Upload statements, get clean spreadsheets for your financial reports.",
        "keywords": ["charity bank statement tool", "non-profit statement converter", "charity treasurer tool"],
        "combo_eligible": True,
        "workflow_detail": "Charity treasurers collect bank statements monthly, reconcile them against donation records and grant income, allocate expenditure to restricted and unrestricted funds, and prepare reports for trustee meetings. Annual accounts must be filed with the Charity Commission, requiring a full year of reconciled bank data.",
        "compliance_requirements": "Charities with income over 250,000 GBP must have accounts independently examined or audited. The Charity Commission requires annual accounts and returns, and SORP (Statement of Recommended Practice) governs how charity accounts are prepared. Fund accounting rules require income to be tracked against specific restricted funds.",
        "software_preferences": ["Xero", "QuickBooks", "LibreOffice Calc"],
        "time_savings": "Saves volunteer treasurers 2-3 hours per month on bank reconciliation, reducing reliance on accounting expertise that small charities often lack",
        "peak_periods": "Financial year-end (often March or December) for annual accounts preparation, plus trustee meeting dates when financial reports are due",
        "industry_jargon": ["restricted funds", "unrestricted funds", "fund accounting", "Charity SORP", "independent examination"],
        "unique_challenges": [
            "Volunteer treasurers with limited accounting knowledge struggling to reconcile bank statements against complex fund structures",
            "Allocating bank transactions to restricted vs unrestricted funds when donation descriptions are vague or missing",
            "Handling multiple bank accounts including deposit accounts and foreign currency accounts for international charities"
        ],
    },
    "contractors": {
        "name": "Contractors & Freelancers",
        "singular": "Contractor",
        "pain_point": "Contractors and freelancers juggling personal and business accounts need to separate expenses for tax purposes.",
        "benefit": "Upload your bank statements and get organised spreadsheets that make expense categorisation and self-assessment tax returns straightforward.",
        "keywords": ["contractor bank statement tool", "freelancer statement converter", "IR35 expense tracker"],
        "combo_eligible": True,
        "workflow_detail": "Contractors download bank statements from both personal and business accounts, separate allowable business expenses from personal spending, categorise expenses by type for self-assessment, and either prepare their own tax return or send organised records to their accountant. Limited company contractors also need to reconcile director's loan accounts.",
        "compliance_requirements": "IR35 legislation requires contractors to demonstrate they are genuinely self-employed. HMRC may request bank statements during IR35 investigations. Contractors operating through limited companies must comply with corporation tax filing and Companies House requirements. Self-employed contractors must register for self-assessment.",
        "software_preferences": ["FreeAgent", "Xero", "QuickBooks Self-Employed"],
        "time_savings": "Saves 1-2 hours per month on expense tracking and categorisation, plus 3-4 hours at year-end when preparing records for self-assessment",
        "peak_periods": "January for self-assessment deadline, April for new tax year planning, and month-end for limited company contractors tracking director's loan accounts",
        "industry_jargon": ["IR35", "director's loan account", "allowable expenses", "flat rate VAT", "deemed employment"],
        "unique_challenges": [
            "Separating business and personal transactions when working through multiple contracts and bank accounts simultaneously",
            "Tracking expenses that are partially allowable (e.g., home office costs, vehicle use) and calculating the business proportion from bank data",
            "Managing director's loan account reconciliation for limited company contractors to avoid unexpected tax liabilities"
        ],
    },
    "dentists": {
        "name": "Dentists",
        "singular": "Dentist",
        "pain_point": "Dental practices handle a mix of NHS and private payments, equipment purchases, and supplier invoices — all flowing through multiple bank accounts.",
        "benefit": "Convert your practice bank statements to Excel instantly, making it easy for your accountant to reconcile NHS payments, private fees, and practice expenses.",
        "keywords": ["dentist bank statement tool", "dental practice accounting", "NHS payment reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Dental practices receive NHS schedule payments monthly from the BSA, process private patient card payments daily, pay dental suppliers for materials and lab work, and manage associate dentist fees. Bank statements must be reconciled against practice management software reports and NHS schedules to ensure all income is accounted for.",
        "compliance_requirements": "Dental practices must comply with CQC registration requirements including financial governance. NHS contract holders must reconcile BSA schedule payments accurately. GDC registration and indemnity insurance costs must be tracked. VAT exemption rules for dental services require careful categorisation of taxable vs exempt income.",
        "software_preferences": ["Sage", "Xero", "Dentally"],
        "time_savings": "Saves 2-3 hours per month reconciling NHS schedule payments against bank statements, plus faster preparation for annual accounts",
        "peak_periods": "Monthly NHS schedule payment reconciliation, quarterly VAT returns for practices with taxable income, and year-end for annual accounts and associate earnings certificates",
        "industry_jargon": ["NHS schedule payment", "UDA value", "associate earnings", "BSA reconciliation", "practice plan"],
        "unique_challenges": [
            "Reconciling NHS BSA schedule payments that bundle multiple patient treatments into single bank deposits with cryptic references",
            "Splitting income between principal and associate dentists when payments flow through a single practice bank account",
            "Tracking equipment finance payments, hire purchase agreements, and capital allowances for expensive dental equipment"
        ],
    },
    "doctors": {
        "name": "Doctors & GPs",
        "singular": "Doctor",
        "pain_point": "GP practices and private doctors deal with NHS reimbursements, private patient fees, and practice expenses across multiple accounts.",
        "benefit": "Automate bank statement conversion for your practice accounts so your accountant can quickly reconcile NHS payments and private income.",
        "keywords": ["doctor bank statement tool", "GP practice accounting", "medical practice finance"],
        "combo_eligible": True,
        "workflow_detail": "GP practices receive Global Sum payments from NHS England, enhanced service payments, QOF achievement payments, and private income. Practice managers reconcile bank statements against PCSE payment schedules, track partner drawings, and manage seniority and locum reimbursements. Year-end requires detailed profit-sharing calculations.",
        "compliance_requirements": "NHS GP contracts require accurate financial reporting to PCNs and ICBs. Partnership agreements dictate profit-sharing arrangements that depend on accurate bank reconciliation. CQC financial governance standards apply, and superannuation contributions must be correctly calculated from practice income.",
        "software_preferences": ["Sage", "Xero", "IRIS GP Accounts"],
        "time_savings": "Saves the practice manager 3-4 hours per month on NHS payment reconciliation and partner drawings tracking",
        "peak_periods": "Year-end for partnership accounts and profit-sharing calculations, quarterly for superannuation returns, and monthly for PCSE payment schedule reconciliation",
        "industry_jargon": ["Global Sum", "QOF payments", "partner drawings", "PCSE schedule", "superannuation"],
        "unique_challenges": [
            "Reconciling complex NHS payment schedules from PCSE that bundle multiple funding streams into single bank deposits",
            "Splitting practice income fairly between partners according to partnership agreement formulas that reference bank transaction data",
            "Tracking locum reimbursements, seniority payments, and enhanced service income that arrive on different schedules and with varying references"
        ],
    },
    "estate-agents": {
        "name": "Estate Agents",
        "singular": "Estate Agent",
        "pain_point": "Estate agents manage client money accounts, commission income, and office expenses — all requiring careful reconciliation for compliance.",
        "benefit": "Convert your client money and office account statements to structured spreadsheets for faster reconciliation and compliance reporting.",
        "keywords": ["estate agent statement tool", "client money reconciliation", "property agent accounting"],
        "combo_eligible": True,
        "workflow_detail": "Estate agents reconcile client money accounts holding tenant deposits and rent, match commission income against completed property sales, and track office operating expenses. Client money accounts must be reconciled separately from office accounts, with detailed records of every client fund movement maintained for compliance.",
        "compliance_requirements": "Estate agents holding client money must comply with the Estate Agents Act 1979 and Client Money Protection schemes. The Property Ombudsman and NAEA Propertymark require regular client money account reconciliation. Anti-money laundering regulations under the Money Laundering Regulations 2017 mandate due diligence on property transactions.",
        "software_preferences": ["Reapit", "Alto by Vebra", "Xero"],
        "time_savings": "Saves 2-3 hours per week on client money account reconciliation and commission tracking across property completions",
        "peak_periods": "Month-end for client money reconciliation, property completion peaks in spring and autumn, and quarterly for CMP scheme reporting",
        "industry_jargon": ["client money account", "completion statement", "exchange of contracts", "CMP scheme", "commission split"],
        "unique_challenges": [
            "Maintaining strict separation between client money and office money accounts, with zero tolerance for mixing funds",
            "Tracking commission income split between agents and referral fees when multiple parties are involved in a sale",
            "Reconciling tenant deposit movements against the relevant deposit protection scheme records"
        ],
    },
    "restaurants": {
        "name": "Restaurants & Hospitality",
        "singular": "Restaurant Owner",
        "pain_point": "Restaurants process hundreds of card transactions daily, plus supplier payments. Reconciling bank statements with POS data is a major headache.",
        "benefit": "Upload your restaurant bank statements and get clean spreadsheets to reconcile against your POS system, suppliers, and HMRC VAT returns.",
        "keywords": ["restaurant bank statement tool", "hospitality accounting", "POS reconciliation tool"],
        "combo_eligible": True,
        "workflow_detail": "Restaurant owners or their bookkeepers reconcile daily card terminal settlements against POS end-of-day reports, match supplier invoice payments, track staff wages and tips, and prepare weekly or monthly cash flow summaries. VAT returns require splitting transactions between standard-rated food sales, zero-rated takeaway, and exempt items.",
        "compliance_requirements": "Restaurants must comply with HMRC VAT rules including the reduced rate for hot food takeaway vs eat-in. Tronc scheme reporting for tips has specific tax treatment. Allergen record-keeping, while not financial, intersects with supplier payment tracking. Making Tax Digital mandates digital VAT records.",
        "software_preferences": ["Xero", "MarketMan", "QuickBooks"],
        "time_savings": "Saves 3-4 hours per week on daily POS-to-bank reconciliation and supplier payment matching for a typical restaurant with 200+ daily transactions",
        "peak_periods": "Daily reconciliation is ongoing, with quarterly VAT returns creating additional pressure. December-January is peak season for hospitality, creating the highest transaction volumes.",
        "industry_jargon": ["POS reconciliation", "card terminal settlement", "tronc scheme", "wet sales", "GP percentage"],
        "unique_challenges": [
            "Matching dozens of card terminal batch settlements per week against POS daily takings when amounts differ due to tips and chargebacks",
            "Handling cash transactions that appear as bank deposits but need reconciliation against POS cash drawer reports",
            "Managing multiple delivery platform payouts (Deliveroo, Uber Eats, Just Eat) that net off commission before depositing, requiring reverse-calculation of gross sales"
        ],
    },
    # --- New professions to reach 250+ pages ---
    "construction": {
        "name": "Construction Companies",
        "singular": "Construction Business",
        "pain_point": "Construction firms juggle CIS deductions, subcontractor payments, material costs, and retention payments — all needing accurate bank reconciliation for HMRC.",
        "benefit": "Convert your bank statements to Excel to quickly match CIS payments, subcontractor invoices, and material costs for VAT returns and CIS submissions.",
        "keywords": ["construction bank statement tool", "CIS statement converter", "builder accounting tool"],
        "combo_eligible": True,
        "workflow_detail": "Construction companies process bank statements to match subcontractor payments against CIS deduction certificates, reconcile material supplier invoices, track retention payments held and released on projects, and prepare monthly CIS returns for HMRC. Project managers often need bank data to verify costs against project budgets.",
        "compliance_requirements": "HMRC's Construction Industry Scheme (CIS) requires monthly returns and correct deduction of tax from subcontractor payments. Reverse charge VAT for construction services must be applied correctly. Health and Safety Executive levies and CITB training levy obligations must also be tracked from bank transactions.",
        "software_preferences": ["Sage 50", "Xero", "Evolution M"],
        "time_savings": "Saves 3-5 hours per month on CIS payment matching and subcontractor reconciliation, reducing errors that can trigger HMRC penalties",
        "peak_periods": "Monthly CIS return deadlines (19th of each month), quarterly VAT returns, and project milestone payment periods when large volumes of subcontractor payments are processed",
        "industry_jargon": ["CIS deduction", "retention payment", "reverse charge VAT", "application for payment", "valuation"],
        "unique_challenges": [
            "Matching CIS deductions against subcontractor invoices when the net payment amount differs from the invoice total due to tax withholding",
            "Tracking retention payments that are held for 12+ months and released in stages, requiring long-term reconciliation against original contract values",
            "Managing cash flow visibility across multiple concurrent projects when all payments flow through one or two bank accounts"
        ],
    },
    "ecommerce": {
        "name": "E-commerce Sellers",
        "singular": "E-commerce Seller",
        "pain_point": "E-commerce sellers receive payments from Amazon, eBay, Shopify, and PayPal — but bank statements lump these together, making reconciliation painful.",
        "benefit": "Convert your bank statements to structured spreadsheets so you can match marketplace payouts, refunds, and fees against your sales records.",
        "keywords": ["ecommerce bank statement tool", "Amazon seller accounting", "Shopify statement converter"],
        "combo_eligible": True,
        "workflow_detail": "E-commerce sellers receive batched payouts from marketplaces and payment processors, then reconcile these against individual order records. They need to match Stripe or PayPal settlements, identify refunds and chargebacks, track advertising spend, and separate marketplace fees from net revenue. Multi-channel sellers must reconcile across several payout sources.",
        "compliance_requirements": "VAT registration is required above the threshold, and e-commerce sellers must account for VAT on sales to different countries under OSS rules. Making Tax Digital applies for VAT returns. HMRC has been increasing scrutiny of online sellers, requiring accurate income records from bank statements.",
        "software_preferences": ["Xero", "A2X", "QuickBooks"],
        "time_savings": "Saves 2-3 hours per week on marketplace payout reconciliation for sellers processing 500+ orders per month across multiple channels",
        "peak_periods": "Black Friday through Christmas (November-December) creates the highest transaction volumes, plus January sales and Prime Day in July. VAT quarter-ends add additional reconciliation pressure.",
        "industry_jargon": ["marketplace payout", "settlement report", "chargeback", "FBA fees", "payment gateway reconciliation"],
        "unique_challenges": [
            "Matching batched marketplace payouts in the bank against individual order-level transactions when platforms like Amazon net off fees, refunds, and advertising costs",
            "Reconciling multiple currency payments from international marketplaces where exchange rates differ between the sale date and payout date",
            "Tracking inventory-related transactions such as FBA storage fees, removal orders, and reimbursements that appear as cryptic line items in bank statements"
        ],
    },
    "financial-advisors": {
        "name": "Financial Advisors (IFAs)",
        "singular": "Financial Advisor",
        "pain_point": "Independent financial advisors reviewing client finances need to analyse bank statements to understand spending patterns, income, and savings capacity.",
        "benefit": "Convert client bank statements to Excel for quick financial analysis, cashflow modelling, and wealth planning presentations.",
        "keywords": ["IFA bank statement tool", "financial advisor statement converter", "wealth planning statement tool"],
        "combo_eligible": True,
        "workflow_detail": "IFAs request bank statements during fact-find meetings to assess client income, expenditure patterns, existing commitments, and savings capacity. They analyse statements to build cashflow models, identify surplus income for investment, and support suitability reports. Statements are also reviewed during annual client reviews to track changes in financial circumstances.",
        "compliance_requirements": "FCA rules require IFAs to conduct thorough fact-finds and demonstrate suitability of advice. Bank statements form part of the evidence base for suitability reports. Consumer Duty obligations require advisors to demonstrate they understand client financial circumstances. Records must be retained for the FCA's required period.",
        "software_preferences": ["Intelliflo", "CashCalc", "Excel"],
        "time_savings": "Saves 30-45 minutes per client fact-find on income and expenditure analysis, with annual reviews completed 20-30 minutes faster per client",
        "peak_periods": "Tax year-end (March-April) for ISA and pension contribution planning, plus September-October for annual review season and January for new year financial planning",
        "industry_jargon": ["fact-find", "suitability report", "attitude to risk", "capacity for loss", "income and expenditure analysis"],
        "unique_challenges": [
            "Analysing client spending patterns from bank statements to determine realistic surplus income for investment without understating living costs",
            "Handling high-net-worth clients with statements from multiple banks, wealth managers, and overseas accounts in different formats",
            "Building accurate cashflow models from statement data that accounts for irregular income sources and seasonal spending variations"
        ],
    },
    "insolvency-practitioners": {
        "name": "Insolvency Practitioners",
        "singular": "Insolvency Practitioner",
        "pain_point": "Insolvency practitioners must review years of bank statements to trace transactions, identify preferences, and report to creditors.",
        "benefit": "Batch-convert years of bank statements into searchable spreadsheets for transaction tracing, preference analysis, and creditor reporting.",
        "keywords": ["insolvency practitioner tool", "IP statement converter", "creditor report statement tool"],
        "combo_eligible": False,
        "workflow_detail": "Insolvency practitioners obtain bank statements from the insolvent entity's banks, convert them to analysable data, trace transactions for the relevant look-back period, identify preferential payments and transactions at undervalue, prepare Statement of Affairs, and produce reports for creditors' meetings. They must reconstruct the financial position at the point of insolvency.",
        "compliance_requirements": "IPs are licensed by recognised professional bodies (IPA, ICAEW, ACCA) and regulated by the Insolvency Service. They must comply with the Insolvency Act 1986, Statement of Insolvency Practice (SIPs), and report to the Secretary of State on director conduct. Detailed financial records must support all claims and distributions to creditors.",
        "software_preferences": ["IPS Cloud", "CaseWare", "Excel with Power Query"],
        "time_savings": "Saves 6-10 hours per insolvency case on initial bank statement analysis, with complex cases involving years of statements saving multiple days of manual work",
        "peak_periods": "Insolvency cases peak during economic downturns and after major creditor actions. Quarter-ends often trigger company failures, and January sees increased personal insolvency filings after Christmas spending.",
        "industry_jargon": ["preferential payment", "transaction at undervalue", "Statement of Affairs", "creditors' voluntary liquidation", "look-back period"],
        "unique_challenges": [
            "Tracing transactions across multiple related entities where directors have moved funds between connected companies before insolvency",
            "Obtaining historic bank statements from banks that may be uncooperative or slow to respond to IP requests, especially for closed accounts",
            "Identifying disguised preferential payments or transactions at undervalue that have been structured to appear as normal trading"
        ],
    },
    "payroll-managers": {
        "name": "Payroll Managers",
        "singular": "Payroll Manager",
        "pain_point": "Payroll managers need to verify salary payments, PAYE deductions, and pension contributions against bank statements every month.",
        "benefit": "Convert your company bank statements to Excel and cross-reference salary payments, HMRC PAYE, and pension fund transfers in minutes.",
        "keywords": ["payroll bank statement tool", "PAYE reconciliation", "payroll statement converter"],
        "combo_eligible": False,
        "workflow_detail": "Payroll managers run monthly payroll, then verify that BACS salary payments have cleared the bank, confirm HMRC PAYE/NIC remittances match the FPS submission, check pension auto-enrolment contributions have been paid to the provider, and reconcile any SSP, SMP, or other statutory payments. They produce reconciliation reports for finance directors.",
        "compliance_requirements": "HMRC requires RTI (Real Time Information) submissions on or before each pay date. PAYE and NIC must be remitted by the 22nd of the following month (electronic) or 19th (cheque). Auto-enrolment pension contributions must be paid to the provider within statutory deadlines. P60s, P11Ds, and P45s must be accurate and timely.",
        "software_preferences": ["Sage Payroll", "BrightPay", "Moneysoft"],
        "time_savings": "Saves 1-2 hours per payroll run on bank reconciliation, ensuring BACS payments, HMRC remittances, and pension contributions are verified within minutes rather than manually checking each transaction",
        "peak_periods": "Monthly payroll dates, plus tax year-end (April) for P60 preparation, July for P11D submissions, and January for third-party data submissions to HMRC",
        "industry_jargon": ["RTI submission", "FPS", "EPS", "BACS payment", "auto-enrolment"],
        "unique_challenges": [
            "Reconciling BACS salary runs where individual employee payments are batched into a single bank transaction that must be matched against payroll reports",
            "Verifying that HMRC PAYE remittances match the amounts calculated in the payroll software when adjustments or corrections span multiple periods",
            "Tracking pension contribution payments across multiple pension providers when the company operates different schemes for different employee groups"
        ],
    },
    "vat-consultants": {
        "name": "VAT Consultants",
        "singular": "VAT Consultant",
        "pain_point": "VAT consultants need to reconcile bank transactions against invoices and receipts to prepare accurate VAT returns and handle HMRC inspections.",
        "benefit": "Convert bank statements and receipts to structured spreadsheets to speed up VAT return preparation and build an audit-ready paper trail.",
        "keywords": ["VAT consultant statement tool", "VAT return bank statement", "Making Tax Digital statement tool"],
        "combo_eligible": False,
        "workflow_detail": "VAT consultants collect bank statements alongside sales and purchase ledgers, cross-reference bank transactions against VAT invoices to verify input and output tax, identify transactions requiring partial exemption calculations, and prepare quarterly or monthly VAT returns. They also review bank data during VAT inspections to substantiate claims.",
        "compliance_requirements": "Making Tax Digital requires VAT-registered businesses to maintain digital records and submit returns through MTD-compatible software. VAT must be accounted for on the correct basis (invoice or cash). HMRC can impose penalties for late or inaccurate returns, and inspections require full transaction-level evidence trails.",
        "software_preferences": ["Sage", "Xero", "TaxCalc"],
        "time_savings": "Saves 2-3 hours per client per VAT quarter on bank-to-ledger reconciliation, with complex partial exemption clients saving up to 5 hours per quarter",
        "peak_periods": "VAT return filing deadlines drive quarterly peaks (one month and 7 days after the VAT period end), with additional pressure during HMRC VAT inspections and voluntary disclosures",
        "industry_jargon": ["input tax", "output tax", "partial exemption", "reverse charge", "MTD bridging software"],
        "unique_challenges": [
            "Identifying transactions subject to reverse charge VAT, especially in construction and cross-border services, from bank statement descriptions alone",
            "Performing partial exemption calculations that require splitting bank transactions between taxable and exempt activities",
            "Reconciling cash accounting VAT clients where the bank payment date determines when VAT is accounted for, not the invoice date"
        ],
    },
    "letting-agents": {
        "name": "Letting Agents",
        "singular": "Letting Agent",
        "pain_point": "Letting agents managing tenant deposits, rent collection, and landlord payments across client money accounts need meticulous reconciliation.",
        "benefit": "Convert your client money account statements to Excel for fast reconciliation of rent receipts, deposit transfers, and management fee deductions.",
        "keywords": ["letting agent statement tool", "rent reconciliation", "client money account converter"],
        "combo_eligible": True,
        "workflow_detail": "Letting agents collect rent from tenants into client money accounts, deduct management fees, pay landlords their net rent, handle deposit protection scheme transfers, and manage maintenance contractor payments. Monthly landlord statements must reconcile exactly with the client money account bank statement, with every penny accounted for.",
        "compliance_requirements": "Letting agents must comply with Client Money Protection (CMP) schemes, which are now mandatory. The Tenant Fees Act 2019 restricts chargeable fees. Deposit protection within 30 days is a legal requirement. ARLA Propertymark members face additional auditing of client money accounts. Anti-money laundering rules apply to property transactions.",
        "software_preferences": ["Goodlord", "Arthur Online", "PayProp"],
        "time_savings": "Saves 3-4 hours per week on client money account reconciliation for a letting agent managing 100+ properties",
        "peak_periods": "Month-end for landlord payment runs and client money reconciliation, plus September-October for the student letting cycle and January-March for renewal season",
        "industry_jargon": ["client money protection", "landlord statement", "tenant deposit scheme", "management fee", "rent arrears"],
        "unique_challenges": [
            "Reconciling hundreds of tenant rent payments arriving with different references and amounts against the property portfolio schedule",
            "Tracking deposit scheme transfers between bank accounts and the relevant protection scheme, ensuring compliance deadlines are met",
            "Managing landlord payment runs where management fees, maintenance deductions, and insurance costs must be netted off before calculating the landlord's payment"
        ],
    },
    "startups": {
        "name": "Startups & Founders",
        "singular": "Startup Founder",
        "pain_point": "Startup founders need to prepare financial summaries for investors, track burn rate, and reconcile multiple accounts — often without a dedicated finance team.",
        "benefit": "Convert your startup bank statements to clean spreadsheets for investor reporting, burn rate analysis, and quick reconciliation without hiring a bookkeeper.",
        "keywords": ["startup bank statement tool", "founder accounting tool", "burn rate tracker"],
        "combo_eligible": True,
        "workflow_detail": "Startup founders download bank statements to calculate monthly burn rate, prepare cash runway reports for board meetings, reconcile Stripe or payment processor deposits against revenue, and provide financial data to investors during due diligence. Many operate without a finance team in early stages, doing reconciliation themselves or with part-time bookkeeper support.",
        "compliance_requirements": "Startups with SEIS/EIS investment must maintain accurate financial records to preserve investor tax relief eligibility. Companies House requires annual accounts, and HMRC requires corporation tax returns. During funding rounds, investors conduct financial due diligence that requires clean, reconciled bank records.",
        "software_preferences": ["Xero", "Fathom", "Mercury"],
        "time_savings": "Saves founders 2-3 hours per month on financial reporting, freeing time for product development and fundraising rather than manual data entry",
        "peak_periods": "Board meeting preparation (typically quarterly), funding round due diligence periods, and year-end for Companies House filings and corporation tax returns",
        "industry_jargon": ["burn rate", "cash runway", "MRR", "unit economics", "cap table"],
        "unique_challenges": [
            "Tracking burn rate accurately when expenses are lumpy and include one-off costs like legal fees for funding rounds mixed with recurring operational costs",
            "Reconciling payment processor deposits (Stripe, GoCardless) that batch multiple customer payments and deduct fees before depositing net amounts",
            "Preparing financial data for investor due diligence on tight timelines when historic bank records have not been systematically maintained"
        ],
    },
    "pharmacies": {
        "name": "Pharmacies",
        "singular": "Pharmacy Owner",
        "pain_point": "Pharmacies receive NHS reimbursements, prescription payments, and retail sales through multiple channels — making bank reconciliation complex.",
        "benefit": "Convert your pharmacy bank statements to Excel to reconcile NHS payments, wholesaler invoices, and retail takings quickly and accurately.",
        "keywords": ["pharmacy bank statement tool", "NHS pharmacy payments", "pharmacy accounting"],
        "combo_eligible": True,
        "workflow_detail": "Pharmacy owners reconcile monthly NHS BSA drug reimbursement payments against dispensing records, match wholesaler invoice payments to purchase orders, track retail OTC sales from card terminal deposits, and manage prescription charge income. The mix of NHS and private income streams flowing through one bank account requires careful separation.",
        "compliance_requirements": "Community pharmacies must comply with the NHS Community Pharmacy Contractual Framework. GPhC registration requires financial governance standards. NHS BSA payments must be reconciled against FP34 endorsement submissions. Controlled drug registers intersect with financial records for audit trail purposes.",
        "software_preferences": ["Sage", "Xero", "Pharmacy Manager"],
        "time_savings": "Saves 2-3 hours per month on NHS reimbursement reconciliation and wholesaler payment matching for a typical community pharmacy",
        "peak_periods": "Monthly NHS BSA payment dates, quarterly VAT returns for retail sales, and year-end for annual accounts. Flu season and pandemic periods create higher transaction volumes.",
        "industry_jargon": ["BSA reimbursement", "FP34 endorsement", "clawback", "wholesaler credit", "dispensing fee"],
        "unique_challenges": [
            "Reconciling NHS BSA reimbursements that include clawbacks and adjustments from previous months, making the net payment difficult to match",
            "Managing cash flow when NHS payments are received weeks after dispensing, while wholesaler invoices require prompt payment",
            "Separating VAT-exempt NHS dispensing income from standard-rated retail sales when both flow through the same bank account"
        ],
    },
    "nurseries": {
        "name": "Nurseries & Childcare",
        "singular": "Nursery Owner",
        "pain_point": "Nurseries manage parent fee payments, government funding allocations, staff wages, and supplier costs — often across multiple accounts.",
        "benefit": "Convert bank statements to spreadsheets to reconcile parent payments against invoices, track government childcare funding, and manage supplier costs.",
        "keywords": ["nursery bank statement tool", "childcare accounting", "nursery fee reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Nursery owners collect fees from parents via standing order or childcare vouchers, receive government-funded hours payments from the local authority, pay staff wages (their largest cost), and manage food, supplies, and premises expenses. Bank statements must be reconciled to track which parents have paid, verify local authority funding received, and ensure staff payroll has cleared.",
        "compliance_requirements": "Ofsted registration requires evidence of sound financial management. Local authority early years funding agreements mandate specific financial reporting. Childcare voucher and Tax-Free Childcare payments require reconciliation against HMRC records. Staff ratio requirements mean payroll costs must be carefully tracked against budget.",
        "software_preferences": ["Xero", "ParentPay", "QuickBooks"],
        "time_savings": "Saves 2-3 hours per month on parent payment tracking and government funding reconciliation for a nursery with 50+ children",
        "peak_periods": "September for new intake reconciliation, January and April for government funding term payments, and monthly for parent fee collection and staff payroll verification",
        "industry_jargon": ["funded hours", "childcare vouchers", "Tax-Free Childcare", "occupancy rate", "staff-to-child ratio"],
        "unique_challenges": [
            "Matching parent payments from multiple sources (bank transfer, childcare vouchers, Tax-Free Childcare top-ups) against individual child invoices",
            "Reconciling local authority funded hours payments that arrive in bulk termly amounts against individual child attendance records",
            "Managing cash flow gaps between when costs are incurred and when government funding or parent payments are received"
        ],
    },
    "veterinarians": {
        "name": "Veterinarians",
        "singular": "Vet Practice Owner",
        "pain_point": "Veterinary practices handle client payments, insurance claims, pharmaceutical purchases, and equipment finance — all needing accurate reconciliation.",
        "benefit": "Convert your practice bank statements to Excel to match client payments, reconcile insurance reimbursements, and track pharmaceutical costs.",
        "keywords": ["vet practice bank statement", "veterinary accounting tool", "vet practice finance"],
        "combo_eligible": True,
        "workflow_detail": "Veterinary practices process daily client payments at reception, receive pet insurance direct settlements, pay pharmaceutical and surgical supply wholesalers, manage locum vet costs, and track lab test fees. Bank statements need reconciliation against practice management software billing records to ensure all invoiced treatments have been paid.",
        "compliance_requirements": "RCVS Practice Standards Scheme requires financial governance for accredited practices. Controlled drug purchasing records must cross-reference with bank payment records. Professional indemnity insurance is mandatory. VMD regulations require pharmaceutical purchase audit trails that intersect with bank transaction records.",
        "software_preferences": ["Sage", "Xero", "VetSolutions RxWorks"],
        "time_savings": "Saves 2-3 hours per week on reconciling client payments and insurance settlements against practice management system billing records",
        "peak_periods": "Monthly for supplier payment reconciliation, spring for vaccination season creating higher transaction volumes, and year-end for annual accounts preparation",
        "industry_jargon": ["direct settlement", "practice management system", "dispensing income", "out-of-hours charges", "client ledger"],
        "unique_challenges": [
            "Reconciling insurance direct settlements where the insurer pays a different amount than invoiced due to policy excess or coverage limits",
            "Matching pharmaceutical wholesaler payments against delivery notes when multiple deliveries are batched into weekly or monthly payment runs",
            "Tracking income from multiple payment methods (card, cash, payment plans) against individual client treatment records in the practice management system"
        ],
    },
    "architects": {
        "name": "Architects",
        "singular": "Architect",
        "pain_point": "Architects bill in stages against project milestones, manage professional indemnity costs, and track expenses across multiple projects simultaneously.",
        "benefit": "Convert your bank statements to structured spreadsheets to match stage payments against projects, track professional fees, and prepare for your accountant.",
        "keywords": ["architect bank statement tool", "architecture practice accounting", "project billing reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Architects invoice clients at RIBA work stages (concept, developed design, technical design, construction), then reconcile bank statements to verify payments received against each project. They track project-specific expenses like survey fees, model-making costs, and travel, and allocate them against project budgets for profitability analysis.",
        "compliance_requirements": "ARB (Architects Registration Board) registration is required to practise. Professional indemnity insurance is mandatory and costs must be tracked. RIBA Chartered Practice standards include financial management requirements. CIS may apply when architects act as principal contractors.",
        "software_preferences": ["Xero", "FreeAgent", "Harvest"],
        "time_savings": "Saves 1-2 hours per month on project payment tracking and expense allocation across concurrent projects for a typical small practice",
        "peak_periods": "Project milestone billing points create irregular peaks, with year-end accounts preparation and professional indemnity insurance renewal adding seasonal pressure",
        "industry_jargon": ["RIBA work stages", "stage payment", "fee proposal", "professional indemnity", "project budget"],
        "unique_challenges": [
            "Allocating bank transactions across multiple concurrent projects when expenses are not clearly labelled with project references",
            "Tracking stage payments that arrive weeks or months after invoicing, requiring long-running reconciliation against original fee proposals",
            "Managing mixed income from direct client work, subcontracted design services, and competition prize money in a single bank account"
        ],
    },
    "photographers": {
        "name": "Photographers & Videographers",
        "singular": "Photographer",
        "pain_point": "Photographers juggle client deposits, final payments, equipment purchases, and travel expenses — often mixing personal and business transactions.",
        "benefit": "Convert your bank statements to Excel to separate business income from personal spending, track equipment costs, and prepare your self-assessment return.",
        "keywords": ["photographer bank statement tool", "creative business accounting", "photographer expense tracker"],
        "combo_eligible": True,
        "workflow_detail": "Photographers collect booking deposits, receive final balance payments before or after shoots, purchase and maintain equipment, pay for travel and accommodation for location work, and license images through stock agencies. They reconcile bank statements to match payments against client bookings, track equipment purchases for capital allowances, and prepare records for self-assessment.",
        "compliance_requirements": "Self-employed photographers must register with HMRC for self-assessment. VAT registration is required above the threshold, with special considerations for digital services sold internationally. Capital allowances apply to equipment purchases. Usage of personal vehicles for business requires mileage tracking against bank records.",
        "software_preferences": ["FreeAgent", "QuickBooks Self-Employed", "HoneyBook"],
        "time_savings": "Saves 1-2 hours per month on income and expense tracking, plus 2-3 hours at year-end separating business and personal transactions for self-assessment",
        "peak_periods": "Wedding season (May-September) creates peak income periods, with January self-assessment deadline creating year-end reconciliation pressure. Equipment purchases often cluster around Black Friday.",
        "industry_jargon": ["booking deposit", "usage rights", "capital allowances", "stock licensing income", "day rate"],
        "unique_challenges": [
            "Separating business equipment purchases from personal spending when using the same bank account and card for both",
            "Tracking partial payments (deposits and balances) against individual client bookings when payment references are inconsistent",
            "Managing irregular income with long gaps between bookings while maintaining accurate cash flow records for tax purposes"
        ],
    },
    "consultants": {
        "name": "Consultants",
        "singular": "Consultant",
        "pain_point": "Consultants managing multiple client engagements need to track retainers, milestone payments, travel expenses, and subcontractor costs across accounts.",
        "benefit": "Convert your bank statements to structured spreadsheets to match client payments, track project expenses, and prepare invoices and tax returns efficiently.",
        "keywords": ["consultant bank statement tool", "consulting practice accounting", "management consultant finance"],
        "combo_eligible": True,
        "workflow_detail": "Consultants invoice clients on retainer, milestone, or time-and-materials bases, then reconcile bank statements to verify payments received. They track project-specific expenses including travel, accommodation, and subcontractor fees, allocate costs to engagements for profitability analysis, and prepare records for quarterly VAT and annual tax returns.",
        "compliance_requirements": "IR35 rules may apply to consultants working through limited companies. VAT registration is common above the threshold. Professional indemnity insurance costs must be tracked. Companies House and corporation tax obligations apply for limited company consultants. Self-employed consultants must file self-assessment returns.",
        "software_preferences": ["Xero", "FreeAgent", "Harvest"],
        "time_savings": "Saves 1-2 hours per month on client payment reconciliation and expense tracking across multiple concurrent engagements",
        "peak_periods": "Month-end for client invoicing and payment chasing, quarterly for VAT returns, and January/April for tax year-end and self-assessment deadlines",
        "industry_jargon": ["retainer", "day rate", "scope creep", "utilisation rate", "engagement letter"],
        "unique_challenges": [
            "Tracking profitability per client engagement when expenses span multiple months and payments are received on different schedules",
            "Reconciling expense reimbursements from clients that arrive as part of larger invoice payments rather than as separate transactions",
            "Managing cash flow forecasting from bank statement data when client payment terms range from 14 to 90 days across different engagements"
        ],
    },
    "recruitment-agencies": {
        "name": "Recruitment Agencies",
        "singular": "Recruitment Agency Owner",
        "pain_point": "Recruitment agencies process candidate placements, client invoices, temporary worker payments, and margin calculations — requiring meticulous bank reconciliation.",
        "benefit": "Convert your agency bank statements to Excel to reconcile placement fees, match temporary worker payments, and track client account balances.",
        "keywords": ["recruitment agency bank statement", "staffing agency accounting", "placement fee reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Recruitment agencies invoice clients for permanent placement fees or weekly/monthly temporary worker charges, pay temporary workers via payroll or umbrella companies, and reconcile margins on each placement. Bank statements must be matched against CRM placement records to verify fees received, track debtor balances, and ensure temporary worker payments are processed correctly.",
        "compliance_requirements": "Employment Agencies Act 1973 and Conduct of Employment Agencies Regulations 2003 govern agency operations. REC (Recruitment and Employment Confederation) members face additional compliance standards. AWR (Agency Workers Regulations) affect temporary worker pay calculations. HMRC employment status rules apply to contractor placements.",
        "software_preferences": ["Xero", "Sage", "Bullhorn"],
        "time_savings": "Saves 3-4 hours per week on placement fee reconciliation and temporary worker payment matching for an agency processing 50+ placements per month",
        "peak_periods": "September for graduate recruitment cycle, January for new year hiring surge, and weekly for temporary worker payroll reconciliation. Quarter-ends create peaks as clients push to fill headcount budgets.",
        "industry_jargon": ["placement fee", "temp margin", "rebate period", "umbrella company", "AWR compliance"],
        "unique_challenges": [
            "Tracking placement fee rebate obligations when candidates leave within the guarantee period, requiring clawback calculations against original bank receipts",
            "Reconciling temporary worker payments through umbrella companies where the agency pays the umbrella and the umbrella pays the worker, creating complex three-way matching",
            "Managing cash flow when client payment terms are 30-60 days but temporary workers must be paid weekly, requiring accurate bank data for working capital management"
        ],
    },
    "pubs-and-bars": {
        "name": "Pubs & Bars",
        "singular": "Pub Owner",
        "pain_point": "Pubs process high volumes of card and cash transactions, brewery payments, and entertainment costs — making daily bank reconciliation essential.",
        "benefit": "Convert your pub bank statements to Excel to reconcile daily takings, match brewery and supplier invoices, and prepare VAT returns accurately.",
        "keywords": ["pub bank statement tool", "bar accounting", "hospitality bank reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Pub owners reconcile daily card terminal settlements and cash banking against till Z-readings, match brewery and drinks supplier invoices against bank debits, track entertainment and licensing costs, and prepare weekly cash flow summaries. Staff tip handling through the tronc system adds another layer of bank transaction complexity.",
        "compliance_requirements": "Licensing Act 2003 compliance requires financial records demonstrating responsible management. HMRC scrutinises cash-heavy businesses more closely, and pubs must maintain detailed records of cash and card takings. VAT on alcohol, food, and soft drinks may have different rates. Machine gaming duty applies to fruit machines.",
        "software_preferences": ["Xero", "QuickBooks", "Stonegate POS"],
        "time_savings": "Saves 2-3 hours per week on daily takings reconciliation and brewery payment matching for a typical pub processing 300+ transactions per day",
        "peak_periods": "Weekends and bank holidays create the highest transaction volumes. December is the peak month for revenue. Summer beer garden season and major sporting events also create reconciliation pressure.",
        "industry_jargon": ["Z-reading", "wet sales", "dry sales", "cellar management", "tied house"],
        "unique_challenges": [
            "Reconciling cash banking deposits against till records when cash handling involves tips, float adjustments, and end-of-night discrepancies",
            "Matching brewery deliveries and invoice payments when tied pub agreements require purchasing from specific suppliers at agreed prices",
            "Tracking machine gaming duty obligations from fruit machine income that appears as cash deposits mixed with regular bar takings"
        ],
    },
    "hair-and-beauty": {
        "name": "Hair & Beauty Salons",
        "singular": "Salon Owner",
        "pain_point": "Hair and beauty salons handle a mix of card payments, cash transactions, product sales, and chair rental income — often without dedicated accounting staff.",
        "benefit": "Convert your salon bank statements to clean spreadsheets to track daily revenue, reconcile supplier payments, and prepare your books for your accountant.",
        "keywords": ["salon bank statement tool", "beauty salon accounting", "hairdresser bank reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Salon owners process daily card and cash payments through their booking system, receive chair rental income from self-employed stylists, pay product suppliers, and manage staff wages. Bank statements are reconciled against the salon booking system's daily takings reports, with product retail sales separated from service income for VAT and profitability analysis.",
        "compliance_requirements": "Self-employed stylists renting chairs must be treated correctly for tax purposes — HMRC scrutinises the employment status of salon workers. VAT may apply to services and retail product sales differently. Health and hygiene compliance under local authority requirements intersects with supply purchase records.",
        "software_preferences": ["Xero", "QuickBooks", "Fresha"],
        "time_savings": "Saves 1-2 hours per week on daily takings reconciliation and supplier payment matching for a salon processing 100+ appointments per week",
        "peak_periods": "Pre-Christmas party season (November-December), pre-wedding season (April-June), and school prom season create peak transaction volumes. Year-end creates reconciliation pressure for tax returns.",
        "industry_jargon": ["chair rental", "column income", "retail vs service split", "appointment booking", "stylist commission"],
        "unique_challenges": [
            "Separating chair rental income from employed stylist revenue when both flow through the same bank account with similar transaction descriptions",
            "Tracking product retail sales separately from service income for accurate VAT treatment and product margin analysis",
            "Managing mixed payment methods (cash, card, gift vouchers, loyalty points) that create complex reconciliation against booking system records"
        ],
    },
    "tradespeople": {
        "name": "Tradespeople (Plumbers, Electricians)",
        "singular": "Tradesperson",
        "pain_point": "Tradespeople receive payments from multiple customers, buy materials from suppliers, and manage van and tool expenses — often on the go with no time for bookkeeping.",
        "benefit": "Convert your bank statements to Excel to track job payments, match material purchases, and get your books ready for self-assessment without hours of data entry.",
        "keywords": ["tradesperson bank statement tool", "plumber accounting", "electrician bank statement converter"],
        "combo_eligible": True,
        "workflow_detail": "Tradespeople receive customer payments via bank transfer, cash, or card, purchase materials from builders' merchants and wholesalers, pay for van fuel and maintenance, and manage tool and equipment costs. Most do their bookkeeping in the evenings or at weekends, matching bank transactions against job quotes and material receipts to calculate profit per job.",
        "compliance_requirements": "Self-employed tradespeople must register with HMRC and file self-assessment returns. CIS applies if working as a subcontractor in construction. Gas Safe registration (gas engineers), NICEIC/NAPIT (electricians), and other trade body memberships have costs that must be tracked. VAT registration is required above the threshold.",
        "software_preferences": ["QuickBooks Self-Employed", "FreeAgent", "Excel"],
        "time_savings": "Saves 2-3 hours per month on expense categorisation and receipt matching, with year-end self-assessment preparation reduced from a full day to under 2 hours",
        "peak_periods": "January for self-assessment deadline, plus seasonal peaks — plumbers are busiest in winter (boiler breakdowns), landscapers in spring/summer, and all trades see a pre-Christmas rush for home improvement projects",
        "industry_jargon": ["day rate", "materials markup", "call-out charge", "retention", "snagging"],
        "unique_challenges": [
            "Tracking material purchases from builders' merchants when receipts are lost or damaged and bank descriptions are generic trade counter references",
            "Separating van and vehicle costs between business and personal use for tax purposes when the same vehicle is used for both",
            "Managing CIS deductions as a subcontractor while also paying subcontractors on larger jobs, creating complex two-way CIS reconciliation"
        ],
    },
    "care-homes": {
        "name": "Care Homes",
        "singular": "Care Home Manager",
        "pain_point": "Care homes receive local authority funding, private fees, and NHS contributions — each with different payment schedules and reconciliation requirements.",
        "benefit": "Convert your care home bank statements to Excel to reconcile council payments, private resident fees, and NHS funding allocations efficiently.",
        "keywords": ["care home bank statement tool", "care home accounting", "local authority payment reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Care home managers reconcile multiple funding streams: local authority spot purchase and block contract payments, private resident fees (often paid by family members), NHS Funded Nursing Care (FNC) contributions, and Continuing Healthcare (CHC) funding. Bank statements must be matched against individual resident funding agreements to track who has paid and identify arrears.",
        "compliance_requirements": "CQC registration requires evidence of financial sustainability and governance. Local authority contracts mandate specific financial reporting. Residents' personal allowances must be managed separately in some cases. Care Act 2014 requirements affect fee-setting and financial assessment processes.",
        "software_preferences": ["Sage", "Xero", "CoolCare"],
        "time_savings": "Saves 3-4 hours per week on funding stream reconciliation for a care home with 40+ residents funded through multiple sources",
        "peak_periods": "Monthly for local authority payment reconciliation, quarterly for CQC financial monitoring, and annually for fee review negotiations with councils and budget planning",
        "industry_jargon": ["funded nursing care", "continuing healthcare", "spot purchase", "block contract", "top-up fees"],
        "unique_challenges": [
            "Reconciling local authority payments that cover multiple residents in a single bulk transfer with minimal transaction reference detail",
            "Tracking individual resident account balances when funding comes from a mix of council, NHS, and private family sources with different payment schedules",
            "Managing fee increases and backdated adjustments when local authority rates change mid-year and reconciling the difference in bank records"
        ],
    },
    "driving-instructors": {
        "name": "Driving Instructors",
        "singular": "Driving Instructor",
        "pain_point": "Driving instructors collect lesson fees via bank transfer, cash, and card — and need to track fuel, vehicle maintenance, and insurance for tax purposes.",
        "benefit": "Convert your bank statements to Excel to separate lesson income from vehicle expenses, making self-assessment tax returns straightforward.",
        "keywords": ["driving instructor bank statement", "ADI accounting tool", "driving school finance"],
        "combo_eligible": False,
        "workflow_detail": "Driving instructors receive lesson payments from multiple students via bank transfer, cash, or card, pay franchise fees to their driving school (if applicable), track fuel costs across hundreds of miles per week, and manage vehicle maintenance, insurance, and dual control fitting costs. Bank statements are used to tally weekly lesson income against their diary and separate business from personal fuel use.",
        "compliance_requirements": "ADI (Approved Driving Instructor) registration with DVSA must be maintained. Self-employed instructors must file self-assessment returns. Mileage and fuel costs must be accurately tracked for HMRC — many use the simplified mileage method. Vehicle insurance must cover business use for driving tuition.",
        "software_preferences": ["QuickBooks Self-Employed", "Excel", "FreeAgent"],
        "time_savings": "Saves 1-2 hours per month on income and expense tracking, reducing year-end self-assessment preparation from several hours to under one hour",
        "peak_periods": "January for self-assessment deadline, plus summer months (June-August) when learners are busiest before university. Test pass surges create income peaks that must be reconciled.",
        "industry_jargon": ["ADI badge", "franchise fee", "dual controls", "test pass rate", "block booking"],
        "unique_challenges": [
            "Tracking lesson income from dozens of students paying different amounts at different frequencies, often with vague bank transfer references",
            "Calculating the business proportion of fuel and vehicle costs when the car is also used for personal mileage outside lesson hours",
            "Managing franchise fee payments or vehicle lease costs that may be bundled with insurance and maintenance in a single monthly debit"
        ],
    },
    "tutors": {
        "name": "Tutors & Educators",
        "singular": "Tutor",
        "pain_point": "Private tutors receiving payments from multiple students and platforms need to track income accurately for self-assessment and potential VAT registration.",
        "benefit": "Convert your bank statements to structured spreadsheets to identify all tutor income sources, track teaching expenses, and prepare your tax return.",
        "keywords": ["tutor bank statement tool", "private tutor accounting", "education freelancer finance"],
        "combo_eligible": False,
        "workflow_detail": "Private tutors receive payments from parents or students via bank transfer, PayPal, or tutoring platforms, purchase teaching materials and resources, pay for venue hire or online platform subscriptions, and track travel costs between students' homes. Bank statements are reviewed monthly to confirm all lesson payments have been received and match against their teaching schedule.",
        "compliance_requirements": "Self-employed tutors must register with HMRC for self-assessment and report all tutoring income, including cash payments. If income exceeds the VAT threshold, registration is required. DBS check costs and professional membership fees are allowable expenses. Tutoring platform income must be declared even when platforms issue their own tax summaries.",
        "software_preferences": ["FreeAgent", "Excel", "QuickBooks Self-Employed"],
        "time_savings": "Saves 1 hour per month on income tracking across multiple payment sources, plus 2 hours at year-end preparing self-assessment records",
        "peak_periods": "September-October for new academic year bookings, January-March for GCSE/A-level exam preparation surge, and January for self-assessment deadline",
        "industry_jargon": ["hourly rate", "block booking discount", "DBS check", "tuition platform", "lesson credits"],
        "unique_challenges": [
            "Tracking income from multiple tutoring platforms (Tutorful, MyTutor, Superprof) that pay at different times and net off platform fees before depositing",
            "Identifying which bank transfers relate to tutoring income when parents send payments with minimal or no reference information",
            "Managing the transition from casual tutoring to a business approaching the VAT threshold, requiring accurate income tracking from bank records"
        ],
    },
    "cleaning-companies": {
        "name": "Cleaning Companies",
        "singular": "Cleaning Company Owner",
        "pain_point": "Cleaning companies manage recurring client payments, staff wages, supplies purchases, and vehicle costs — often across residential and commercial contracts.",
        "benefit": "Convert your bank statements to Excel to reconcile client payments against contracts, track staff costs, and manage supplier accounts efficiently.",
        "keywords": ["cleaning company bank statement", "cleaning business accounting", "janitorial service finance"],
        "combo_eligible": True,
        "workflow_detail": "Cleaning company owners reconcile client contract payments (often recurring standing orders) against their client list, pay cleaning staff wages weekly or monthly, purchase cleaning supplies and equipment, and manage vehicle fuel and maintenance costs. Bank statements are used to verify which clients have paid, identify late payers, and ensure staff payroll has cleared correctly.",
        "compliance_requirements": "Cleaning companies must comply with HMRC employer obligations including PAYE, National Insurance, and auto-enrolment pensions. Minimum wage compliance is closely monitored in the cleaning sector. Public liability insurance is essential for commercial contracts. VAT registration is required above the threshold.",
        "software_preferences": ["Xero", "QuickBooks", "Swept"],
        "time_savings": "Saves 2-3 hours per week on client payment tracking and staff payroll reconciliation for a cleaning company with 20+ regular clients",
        "peak_periods": "Month-end for client invoicing and payment chasing, weekly for staff payroll processing, and January for self-assessment. Spring cleaning season and year-end deep cleans create higher transaction volumes.",
        "industry_jargon": ["contract rate", "hourly rate", "deep clean", "consumables", "site visit"],
        "unique_challenges": [
            "Tracking recurring client payments when dozens of standing orders arrive on different dates with varying or missing references",
            "Managing staff payroll costs across multiple client sites when cleaners work different hours at different locations each week",
            "Handling the mix of residential (often cash or bank transfer) and commercial (invoice-based) payment methods in the same bank account"
        ],
    },
    "fitness-trainers": {
        "name": "Personal Trainers & Gyms",
        "singular": "Personal Trainer",
        "pain_point": "Personal trainers and gym owners handle membership payments, class fees, equipment purchases, and venue hire — often through multiple payment apps.",
        "benefit": "Convert your bank statements to structured spreadsheets to track membership income, match equipment purchases, and prepare your books for your accountant.",
        "keywords": ["personal trainer bank statement", "gym accounting tool", "fitness business finance"],
        "combo_eligible": True,
        "workflow_detail": "Personal trainers and gym owners collect payments via direct debit for memberships, receive individual session payments through apps like PayPal or bank transfer, pay for equipment and venue hire, and track CPD course costs. Bank statements are reconciled against membership management software or booking systems to verify all client payments have been received.",
        "compliance_requirements": "Self-employed personal trainers must register with HMRC for self-assessment. REPs (Register of Exercise Professionals) membership and CPD costs are allowable expenses. Public liability and professional indemnity insurance are industry requirements. Gym operators must comply with health and safety regulations and may need premises licenses.",
        "software_preferences": ["Xero", "QuickBooks", "Mindbody"],
        "time_savings": "Saves 1-2 hours per week on membership payment tracking and session income reconciliation for a trainer with 30+ regular clients",
        "peak_periods": "January for New Year fitness resolutions creating a surge in new memberships, September for back-to-routine sign-ups, and pre-summer (April-May) for body transformation programmes",
        "industry_jargon": ["session rate", "block booking", "membership direct debit", "PT package", "class pass"],
        "unique_challenges": [
            "Tracking membership direct debit payments that fail, retry, or are cancelled, requiring reconciliation against the membership management system",
            "Managing multiple payment methods (app payments, bank transfer, cash) from individual PT clients with inconsistent payment references",
            "Separating gym facility income from personal training session income when both revenue streams flow through the same bank account"
        ],
    },
    # --- US Professions ---
    "cpas": {
        "name": "CPAs (Certified Public Accountants)",
        "singular": "CPA",
        "pain_point": "CPAs managing multiple client engagements need to process bank statements from dozens of different US banks for tax preparation, audit support, and financial reviews.",
        "benefit": "BankScan AI converts any US bank statement PDF to Excel instantly — supporting Chase, Bank of America, Wells Fargo, Citi, and 30+ US banks for seamless client work.",
        "keywords": ["CPA bank statement tool", "certified public accountant statement converter", "CPA PDF to Excel"],
        "combo_eligible": True,
        "workflow_detail": "CPAs receive client bank statements for tax return preparation, financial statement compilation and review, and audit procedures. They reconcile bank balances to general ledger accounts, trace transactions for substantive testing during audits, and prepare bank reconciliation workpapers. During tax season, they process hundreds of client statements to verify income and identify deductible expenses.",
        "compliance_requirements": "CPAs must comply with AICPA professional standards and state board licensing requirements. GAAP and GAAS standards govern financial statement preparation and auditing. IRS Circular 230 governs tax practice. Peer review requirements apply to firms performing audits. Client records must be retained per state-specific rules, typically 5-7 years.",
        "software_preferences": ["QuickBooks Online", "Sage Intacct", "CCH Axcess"],
        "time_savings": "Saves an average of 30 minutes per client per engagement on bank statement data entry, or 10+ hours per week during busy season for a firm with 200+ clients",
        "peak_periods": "January through April 15 for individual tax returns, March 15 for S-Corp and partnership returns, September-October for extended returns, and December-January for year-end audit fieldwork",
        "industry_jargon": ["bank confirmation", "reconciling items", "substantive testing", "adjusted trial balance", "workpaper"],
        "unique_challenges": [
            "Processing bank statements from dozens of different US banks with varying PDF formats across a large and diverse client portfolio",
            "Performing bank confirmations and reconciliations during audits where discrepancies between bank statements and general ledger must be traced and documented",
            "Managing the volume surge during tax season when hundreds of client statements need processing within a compressed 3-month window"
        ],
    },
    "enrolled-agents": {
        "name": "Enrolled Agents",
        "singular": "Enrolled Agent",
        "pain_point": "Enrolled Agents representing clients before the IRS need to review bank statements for tax return preparation, audit defense, and compliance verification.",
        "benefit": "Batch-convert client bank statements to Excel for fast income verification, deduction substantiation, and IRS examination responses.",
        "keywords": ["enrolled agent bank statement tool", "EA statement converter", "IRS representation statement tool"],
        "combo_eligible": True,
        "workflow_detail": "Enrolled Agents review client bank statements to verify income reported on tax returns, identify unreported income during IRS examinations, substantiate deductions claimed, and prepare documentation for audit defense. They compare bank deposits against 1099s and W-2s to identify discrepancies and help clients respond to IRS notices and correspondence audits.",
        "compliance_requirements": "Enrolled Agents are federally licensed by the IRS under Circular 230 and must complete 72 hours of continuing education every 3 years. They have unlimited practice rights before the IRS. They must maintain client confidentiality under IRC Section 7216 and follow IRS e-file requirements. PTIN renewal is required annually.",
        "software_preferences": ["Drake Tax", "Lacerte", "ProConnect Tax"],
        "time_savings": "Saves 1-2 hours per IRS examination response on bank statement analysis, plus 20-30 minutes per tax return during filing season on income verification",
        "peak_periods": "January through April 15 for tax filing season, August-October for extended returns, and year-round for IRS examination responses and collections cases",
        "industry_jargon": ["IRS examination", "correspondence audit", "bank deposit analysis", "Circular 230", "power of attorney"],
        "unique_challenges": [
            "Performing bank deposit analysis during IRS examinations to explain every deposit and prove non-taxable sources like transfers, loans, and gifts",
            "Reconstructing income from bank statements for clients who have not kept adequate books and records, as required during IRS audits",
            "Analyzing multi-year bank statements to identify patterns that support or refute IRS proposed adjustments in examination cases"
        ],
    },
    "tax-preparers": {
        "name": "Tax Preparers",
        "singular": "Tax Preparer",
        "pain_point": "Tax preparers during filing season need to process hundreds of client bank statements to verify income, identify deductions, and prepare accurate returns.",
        "benefit": "Convert client bank statements to organized Excel spreadsheets in seconds — verify W-2 income, find deductible expenses, and prepare returns faster during tax season.",
        "keywords": ["tax preparer bank statement tool", "tax season statement converter", "1040 bank statement tool"],
        "combo_eligible": True,
        "workflow_detail": "Tax preparers collect bank statements alongside W-2s, 1099s, and expense receipts during client intake. They review statements to verify reported income, identify deductible expenses like business costs and charitable donations, calculate estimated tax payments made, and ensure all 1099-reported income matches bank deposits. High-volume preparers process 50+ returns per week during peak season.",
        "compliance_requirements": "Tax preparers must obtain a PTIN from the IRS and comply with due diligence requirements under IRC Section 6695. Paid preparers must sign returns and include their PTIN. State-level requirements vary, with some states requiring registration, testing, or continuing education. IRS e-file mandates apply to preparers filing more than 10 returns.",
        "software_preferences": ["TurboTax Pro", "Drake Tax", "TaxSlayer Pro"],
        "time_savings": "Saves 15-20 minutes per return on income verification and expense identification, adding up to 8-12 hours per week during peak tax season",
        "peak_periods": "January through April 15 is the primary filing season, with February being the peak month as W-2s arrive. August-October for extended returns. Year-round for amended returns and prior-year filings.",
        "industry_jargon": ["W-2 matching", "1099 reconciliation", "Schedule C", "itemized deductions", "estimated tax payments"],
        "unique_challenges": [
            "Verifying that all 1099-reported income (interest, dividends, contractor payments) appears in client bank statements and is properly reported",
            "Identifying deductible expenses from bank statements for self-employed clients who have not separated business and personal accounts",
            "Processing a high volume of client statements in a compressed timeframe during tax season with varying bank formats and statement periods"
        ],
    },
    "real-estate-agents-us": {
        "name": "Real Estate Agents",
        "singular": "Real Estate Agent",
        "pain_point": "Real estate agents and brokers need to review buyer bank statements for pre-qualification, track commission income, and manage escrow account reconciliation.",
        "benefit": "Convert bank statements to clean spreadsheets for buyer pre-qualification reviews, commission tracking, and escrow reconciliation.",
        "keywords": ["real estate agent bank statement tool", "realtor statement converter", "escrow reconciliation tool"],
        "combo_eligible": True,
        "workflow_detail": "Real estate agents review buyer bank statements to verify down payment funds and assess financial readiness for pre-qualification letters. They track commission checks from closings, reconcile escrow account activity, manage marketing expenses and MLS dues, and prepare Schedule C documentation for their tax preparer. Broker-agents must also reconcile their split with the brokerage.",
        "compliance_requirements": "State real estate commissions regulate agent licensing and escrow account management. NAR Code of Ethics applies to Realtor members. Escrow accounts are subject to state-specific trust accounting rules. Anti-money laundering requirements under FinCEN apply to real estate transactions over $300,000 in certain markets.",
        "software_preferences": ["QuickBooks", "Dotloop", "Excel"],
        "time_savings": "Saves 30-45 minutes per buyer pre-qualification on bank statement review, plus 1-2 hours per month on commission and expense tracking",
        "peak_periods": "Spring and summer home-buying season (March-August), with closings peaking in June-July. Year-end for tax preparation and commission reconciliation against 1099-MISC forms.",
        "industry_jargon": ["escrow account", "commission split", "closing statement", "pre-qualification", "earnest money"],
        "unique_challenges": [
            "Reviewing buyer bank statements to verify seasoned funds for down payment while identifying red flags like large unexplained deposits that lenders will question",
            "Tracking commission income from multiple closings with varying split arrangements and referral fees deducted at different points",
            "Reconciling escrow account activity when earnest money deposits, repairs, and closing adjustments create complex fund flows across multiple parties"
        ],
    },
    "property-managers": {
        "name": "Property Managers",
        "singular": "Property Manager",
        "pain_point": "Property managers handling rent collection, maintenance expenses, HOA dues, and owner distributions across multiple properties need meticulous bank reconciliation.",
        "benefit": "Convert property management bank statements to Excel to reconcile rent payments, track maintenance costs, and prepare owner distribution reports.",
        "keywords": ["property manager bank statement tool", "rental property accounting", "property management reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Property managers collect rent from tenants via ACH or check, pay property owners their distributions after deducting management fees and expenses, coordinate maintenance vendor payments, handle security deposit accounts, and reconcile HOA dues. Monthly owner statements must be prepared from bank data showing all income and expenses for each property in the portfolio.",
        "compliance_requirements": "State property management licensing requirements vary but generally require trust account compliance for tenant funds. Security deposit laws mandate separate accounts in many states. Fair housing regulations intersect with financial records. IRS 1099 reporting is required for owner distributions and vendor payments over $600.",
        "software_preferences": ["AppFolio", "Buildium", "QuickBooks"],
        "time_savings": "Saves 4-6 hours per week on rent collection reconciliation and owner distribution preparation for a property manager with 100+ units",
        "peak_periods": "First week of each month for rent collection reconciliation, mid-month for owner distributions, and year-end for 1099 preparation and annual owner financial statements",
        "industry_jargon": ["owner distribution", "CAM charges", "security deposit trust", "rent roll", "vacancy loss"],
        "unique_challenges": [
            "Reconciling rent payments from dozens of tenants when ACH and check payments arrive on different dates with inconsistent reference information",
            "Managing security deposit trust accounts that must be maintained separately per state law and reconciled against individual tenant balances",
            "Preparing accurate owner statements that allocate shared expenses (like landscape maintenance) across multiple properties in a portfolio"
        ],
    },
    "attorneys": {
        "name": "Attorneys",
        "singular": "Attorney",
        "pain_point": "Attorneys handling divorce, bankruptcy, personal injury, and business litigation cases need to analyze bank statements for financial discovery and evidence preparation.",
        "benefit": "Convert client and opposing party bank statements to searchable Excel spreadsheets for faster discovery review, asset tracing, and litigation support.",
        "keywords": ["attorney bank statement tool", "legal statement converter", "litigation support statement tool"],
        "combo_eligible": True,
        "workflow_detail": "Attorneys receive bank statements during discovery in litigation, review them for asset tracing in divorce and bankruptcy cases, analyze financial evidence in fraud and embezzlement matters, and manage IOLTA trust accounts for client funds. They convert statements to structured data for timeline construction, hidden asset identification, and expert witness preparation.",
        "compliance_requirements": "State bar rules require attorneys to maintain IOLTA (Interest on Lawyers' Trust Accounts) with strict trust accounting requirements. ABA Model Rules of Professional Conduct govern client fund handling. Failure to properly reconcile trust accounts is one of the leading causes of attorney discipline. Three-way reconciliation of trust accounts is typically required monthly.",
        "software_preferences": ["Clio", "MyCase", "QuickBooks for Lawyers"],
        "time_savings": "Saves 2-4 hours per case on financial discovery analysis, with complex asset tracing cases saving a full day or more of manual statement review",
        "peak_periods": "Court filing deadlines drive unpredictable peaks. Divorce cases surge in January (post-holiday filings). Bankruptcy filings increase during economic downturns. Trust account reconciliation is required monthly.",
        "industry_jargon": ["IOLTA trust account", "discovery production", "asset tracing", "three-way reconciliation", "forensic analysis"],
        "unique_challenges": [
            "Analyzing opposing party bank statements during discovery to identify hidden assets, undisclosed income, or fraudulent transfers",
            "Maintaining strict IOLTA trust account compliance with monthly three-way reconciliation between bank statement, trust ledger, and client ledger",
            "Processing large volumes of bank statements produced during discovery in commercial litigation where years of financial records must be analyzed systematically"
        ],
    },
    "medical-practices": {
        "name": "Medical Practices",
        "singular": "Medical Practice Owner",
        "pain_point": "Medical practices receive payments from insurance companies, Medicare, Medicaid, and patients — each with different reimbursement schedules and reconciliation requirements.",
        "benefit": "Convert practice bank statements to Excel to reconcile insurance reimbursements, patient payments, and vendor costs for accurate practice financial management.",
        "keywords": ["medical practice bank statement tool", "healthcare accounting", "insurance reimbursement reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Medical practice administrators reconcile ERA (Electronic Remittance Advice) payments from insurance companies against claims submitted, verify patient co-pay and deductible collections, track Medicare and Medicaid reimbursements, and match vendor payments for medical supplies and equipment. Bank statements are cross-referenced against practice management system billing reports to identify underpayments and denials.",
        "compliance_requirements": "Medical practices must comply with HIPAA for financial record security, Medicare and Medicaid billing regulations, and Stark Law and Anti-Kickback Statute requirements. State medical board requirements affect licensing costs. IRS 1099 reporting is required for independent contractor physicians. Malpractice insurance costs must be tracked accurately.",
        "software_preferences": ["QuickBooks", "Kareo", "athenahealth"],
        "time_savings": "Saves 4-6 hours per week on insurance payment reconciliation and patient payment posting for a practice with 5+ providers",
        "peak_periods": "Monthly for insurance ERA reconciliation, quarterly for Medicare cost reports (for participating practices), and year-end for 1099 preparation and annual financial statements",
        "industry_jargon": ["ERA payment", "EOB", "claim denial", "CPT code", "accounts receivable aging"],
        "unique_challenges": [
            "Reconciling insurance ERA payments that bundle dozens of patient claims into single bank deposits with complex adjustment codes for denials and partial payments",
            "Tracking patient responsibility balances (co-pays, deductibles, coinsurance) that arrive as individual small payments weeks or months after the visit",
            "Managing multiple payer contracts with different fee schedules and reimbursement rates, requiring bank deposits to be matched against the correct expected payment amounts"
        ],
    },
    "nonprofit-organizations": {
        "name": "Nonprofit Organizations",
        "singular": "Nonprofit Director",
        "pain_point": "Nonprofits need accurate bank reconciliation for grant reporting, donor accountability, Form 990 preparation, and board financial presentations.",
        "benefit": "Convert nonprofit bank statements to structured spreadsheets for grant expense tracking, donor fund reconciliation, and Form 990 preparation.",
        "keywords": ["nonprofit bank statement tool", "501c3 accounting", "grant reporting statement converter"],
        "combo_eligible": True,
        "workflow_detail": "Nonprofit finance staff reconcile bank statements against donor management systems to verify donation receipts, track grant expenditures against budget line items for funder reporting, prepare monthly board financial reports, allocate expenses between programs and administration, and compile data for annual Form 990 preparation. Restricted fund tracking requires each grant's spending to be isolated.",
        "compliance_requirements": "501(c)(3) organizations must file Form 990 annually with the IRS, which requires detailed financial disclosure. Grant agreements impose specific financial reporting requirements and allowable cost rules. State charitable solicitation registration may apply. Single Audit requirements under OMB Uniform Guidance apply to organizations spending $750,000+ in federal funds.",
        "software_preferences": ["QuickBooks Nonprofit", "Sage Intacct", "Aplos"],
        "time_savings": "Saves 3-4 hours per month on grant expense tracking and donor reconciliation, plus 8-10 hours during annual Form 990 preparation",
        "peak_periods": "Year-end for annual Form 990 filing and audit preparation, December for year-end giving reconciliation, and grant reporting deadlines throughout the year that vary by funder",
        "industry_jargon": ["Form 990", "restricted vs unrestricted", "functional expense allocation", "in-kind donation", "grant drawdown"],
        "unique_challenges": [
            "Tracking expenses by grant and program when bank transactions do not inherently indicate which funding source should be charged",
            "Reconciling year-end giving surges when hundreds of donations arrive in December with varying payment methods and recognition timing",
            "Preparing functional expense allocations for Form 990 that accurately split costs between program, management, and fundraising using bank transaction data"
        ],
    },
    "trucking-companies": {
        "name": "Trucking & Logistics",
        "singular": "Trucking Company Owner",
        "pain_point": "Trucking companies manage fuel card transactions, load payments, equipment financing, and IFTA tax reporting — all requiring precise bank reconciliation.",
        "benefit": "Convert bank statements to Excel to match load payments, reconcile fuel card charges, and prepare IFTA quarterly tax filings accurately.",
        "keywords": ["trucking company bank statement", "logistics accounting tool", "IFTA statement converter"],
        "combo_eligible": True,
        "workflow_detail": "Trucking company owners reconcile load payments from brokers and shippers against rate confirmations, match fuel card transactions to individual trucks and routes, track equipment lease and loan payments, reconcile ELD (Electronic Logging Device) data against driver pay, and prepare IFTA quarterly fuel tax filings that require precise mileage-by-state calculations correlated with fuel purchases.",
        "compliance_requirements": "FMCSA regulations require financial responsibility (insurance) documentation. IFTA (International Fuel Tax Agreement) mandates quarterly fuel tax returns reconciling fuel purchased vs miles driven per state. UCR (Unified Carrier Registration) fees must be tracked. DOT compliance costs including drug testing and CSA scores have financial implications. IRS Form 2290 heavy vehicle use tax applies.",
        "software_preferences": ["QuickBooks", "TruckingOffice", "AXON"],
        "time_savings": "Saves 4-6 hours per quarter on IFTA tax preparation by automating fuel purchase extraction from bank statements, plus 2-3 hours per week on load payment reconciliation",
        "peak_periods": "IFTA quarterly filing deadlines (January 31, April 30, July 31, October 31), plus monthly for driver settlements and weekly for load payment verification",
        "industry_jargon": ["rate confirmation", "lumper fee", "deadhead miles", "IFTA decal", "driver settlement"],
        "unique_challenges": [
            "Matching fuel card transactions from bank statements against specific trucks and routes for accurate IFTA state-by-state fuel tax calculations",
            "Reconciling load payments from freight brokers that may arrive weeks after delivery with deductions for detention, lumper fees, or chargebacks",
            "Tracking owner-operator settlements that combine multiple loads, deduct fuel advances and insurance, and require precise per-mile cost calculations"
        ],
    },
    "auto-dealers": {
        "name": "Auto Dealers",
        "singular": "Auto Dealer",
        "pain_point": "Auto dealerships process floor plan financing, vehicle sales, trade-ins, F&I product income, and manufacturer incentives — requiring complex bank reconciliation.",
        "benefit": "Convert dealership bank statements to spreadsheets for floor plan reconciliation, sales tracking, and manufacturer incentive verification.",
        "keywords": ["auto dealer bank statement tool", "dealership accounting", "floor plan reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Auto dealership controllers reconcile floor plan financing curtailments against vehicle sales, track F&I (Finance and Insurance) product income, verify manufacturer incentive and holdback payments, match customer financing deposits from lenders, and manage trade-in payoffs. Bank statements are reconciled daily against the DMS (Dealer Management System) to ensure all vehicle transactions are properly recorded.",
        "compliance_requirements": "Auto dealers must comply with FTC Safeguards Rule for customer financial data protection, state dealer licensing requirements, and IRS Form 8300 reporting for cash transactions over $10,000. Floor plan lenders conduct regular audits. OFAC screening requirements apply. State DMV title and registration financial records must be maintained.",
        "software_preferences": ["CDK Global", "Reynolds and Reynolds", "QuickBooks"],
        "time_savings": "Saves 3-5 hours per week on floor plan reconciliation and deal funding verification for a dealership selling 100+ vehicles per month",
        "peak_periods": "Month-end for manufacturer reporting and floor plan audits, year-end for inventory valuation and LIFO calculations, and daily for deal funding verification",
        "industry_jargon": ["floor plan curtailment", "deal jacket", "holdback", "F&I income", "trade-in payoff"],
        "unique_challenges": [
            "Reconciling floor plan curtailment payments against individual vehicle sales when the floor plan lender and selling bank may process transactions on different dates",
            "Tracking manufacturer incentive payments that arrive as bulk deposits covering multiple programs (holdbacks, bonuses, co-op advertising) with minimal line-item detail",
            "Managing the complex flow of funds in each vehicle deal involving customer down payments, lender funding, trade-in payoffs, and F&I product commissions"
        ],
    },
    "amazon-sellers": {
        "name": "Amazon & FBA Sellers",
        "singular": "Amazon Seller",
        "pain_point": "Amazon FBA sellers receive disbursements that bundle sales, refunds, FBA fees, and advertising costs — making bank reconciliation against Amazon reports challenging.",
        "benefit": "Convert your bank statements to Excel and cross-reference Amazon disbursements with your Seller Central reports for accurate profit calculations and tax reporting.",
        "keywords": ["Amazon seller bank statement tool", "FBA accounting", "Amazon disbursement reconciliation"],
        "combo_eligible": True,
        "workflow_detail": "Amazon FBA sellers reconcile biweekly Amazon disbursements against Seller Central settlement reports, identify individual order-level revenue and fees, track advertising spend (PPC campaigns) against bank debits, match refund and reimbursement credits, and verify FBA storage and fulfillment fees. Multi-channel sellers also reconcile Walmart, eBay, and Shopify payouts simultaneously.",
        "compliance_requirements": "Amazon sellers must comply with state sales tax nexus requirements, which vary by state and may require collection and remittance in dozens of jurisdictions. IRS 1099-K reporting applies to marketplace sellers. State income tax filing obligations may arise from economic nexus. Inventory-based businesses may need to file personal property tax returns in some states.",
        "software_preferences": ["QuickBooks", "A2X", "Seller Board"],
        "time_savings": "Saves 2-3 hours per disbursement cycle on Amazon settlement reconciliation, or 4-6 hours per month for a seller with $50,000+ monthly revenue",
        "peak_periods": "Q4 (October-December) for holiday selling season creates massive transaction volumes. Prime Day in July is another peak. Monthly sales tax filing deadlines and quarterly estimated tax payments add reconciliation pressure.",
        "industry_jargon": ["disbursement", "settlement report", "ASIN", "FBA fulfillment fee", "reimbursement claim"],
        "unique_challenges": [
            "Decomposing Amazon disbursements that net together hundreds of orders, returns, advertising charges, and fees into individual transaction-level data for accounting",
            "Tracking inventory reimbursements for lost or damaged FBA items that appear as credits in disbursements weeks after the original issue",
            "Managing multi-state sales tax obligations when Amazon collects in some states but not others, requiring bank statement data to verify which transactions have tax collected vs owed"
        ],
    },
    "cannabis-businesses": {
        "name": "Cannabis Businesses",
        "singular": "Cannabis Business Owner",
        "pain_point": "Cannabis businesses face unique banking challenges with limited banking access, cash-heavy operations, and strict IRS Section 280E compliance requirements.",
        "benefit": "Convert bank statements to structured spreadsheets for 280E compliance, COGS tracking, and state regulatory reporting where traditional accounting integrations are limited.",
        "keywords": ["cannabis business bank statement", "280E compliance tool", "marijuana business accounting"],
        "combo_eligible": False,
        "workflow_detail": "Cannabis business owners reconcile bank statements (when banking access is available) against POS system sales reports, track COGS meticulously for Section 280E compliance, manage cash deposits from predominantly cash-based operations, and prepare state regulatory reports. Seed-to-sale tracking systems must correlate with financial records for compliance audits.",
        "compliance_requirements": "IRS Section 280E prohibits cannabis businesses from deducting ordinary business expenses, making COGS the only deductible category. State licensing requirements mandate detailed financial record-keeping and reporting. FinCEN SAR (Suspicious Activity Report) filing requirements apply to banks serving cannabis businesses. State seed-to-sale tracking systems require financial data correlation.",
        "software_preferences": ["QuickBooks", "Cannabis-specific ERP (Distru)", "Excel"],
        "time_savings": "Saves 3-4 hours per week on cash deposit reconciliation and COGS tracking, which is critical given the limited deductibility under Section 280E",
        "peak_periods": "Quarterly estimated tax payments (which are unusually high due to 280E), state licensing renewal periods, and month-end for state regulatory reporting. April 15 and October 15 for federal tax filing.",
        "industry_jargon": ["Section 280E", "COGS allocation", "seed-to-sale tracking", "SAR filing", "safe harbor banking"],
        "unique_challenges": [
            "Maximizing COGS allocation under Section 280E to reduce the effective tax rate, which requires meticulous bank statement analysis to classify every transaction",
            "Managing cash-heavy operations where large cash deposits trigger SAR filings and require detailed documentation matching deposits to POS sales records",
            "Maintaining banking relationships when banks may unexpectedly close cannabis business accounts, requiring rapid transition to new financial institutions and re-establishing transaction history"
        ],
    },
}

# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------
FORMATS = {
    "excel": {
        "name": "Excel",
        "extension": ".xlsx",
        "icon": "table",
        "description": "Microsoft Excel (.xlsx) is the most versatile output format — ideal for sorting, filtering, pivot tables, and custom formulas on your bank data.",
        "best_for": "Accountants doing manual analysis, auditors sampling transactions, and anyone who needs to manipulate data with formulas or charts",
        "limitations": "Requires Microsoft Excel or a compatible app (Google Sheets, LibreOffice). Not directly importable into most accounting software without column mapping.",
        "import_compatible": ["Xero (via CSV re-save)", "QuickBooks (via CSV re-save)", "Sage", "IRIS"],
        "technical_notes": "BankScan AI outputs .xlsx files with formatted headers, auto-sized columns, date cells (not text), and numeric amounts — so SUM, VLOOKUP, and pivot tables work immediately without reformatting.",
        "unique_advantages": "Supports multiple sheets per workbook, conditional formatting for flagging large transactions, and embedded formulas for running balance verification.",
    },
    "csv": {
        "name": "CSV",
        "extension": ".csv",
        "icon": "file-text",
        "description": "Comma-Separated Values (.csv) is the universal import format accepted by virtually every accounting platform, database, and analysis tool.",
        "best_for": "Importing transactions into accounting software (Xero, QuickBooks, FreeAgent), bulk data processing, and integration with custom scripts or databases",
        "limitations": "No formatting, no formulas, no multiple sheets. Date and number formats can be misinterpreted by some applications depending on locale settings.",
        "import_compatible": ["Xero", "QuickBooks", "Sage", "FreeAgent", "KashFlow", "Wave", "Zoho Books", "FreshBooks", "Clear Books", "Pandle"],
        "technical_notes": "BankScan AI outputs UTF-8 encoded CSV with ISO date format (YYYY-MM-DD) by default, with options for DD/MM/YYYY or MM/DD/YYYY. Amounts use decimal points, no thousands separators.",
        "unique_advantages": "Smallest file size, opens in any text editor, and is the most widely accepted import format across all accounting platforms worldwide.",
    },
    "google-sheets": {
        "name": "Google Sheets",
        "extension": "",
        "icon": "grid",
        "description": "Google Sheets output creates a cloud-based spreadsheet accessible from any browser — perfect for collaborative review and sharing with clients or colleagues.",
        "best_for": "Teams collaborating on bank reconciliation, accountants sharing working papers with clients, and remote workers who need cloud access to converted statements",
        "limitations": "Requires a Google account. Large statements (10,000+ rows) may be slow. Not suitable for offline work without prior download.",
        "import_compatible": ["Can be downloaded as CSV or Excel for import into any accounting software"],
        "technical_notes": "BankScan AI creates a shareable Google Sheet with view-only permissions by default. The sheet includes formatted headers, frozen top row, and auto-filters enabled.",
        "unique_advantages": "Real-time collaboration, comment threads on specific transactions, version history, and the ability to share with clients via a simple link without email attachments.",
    },
    "ofx": {
        "name": "OFX",
        "extension": ".ofx",
        "icon": "file",
        "description": "Open Financial Exchange (.ofx) is a standardised financial data format designed specifically for importing transactions into personal finance and accounting software.",
        "best_for": "Direct import into QuickBooks Desktop, Microsoft Money, GnuCash, and other personal finance applications that accept OFX bank feeds",
        "limitations": "Not human-readable (XML-based). Limited support in cloud accounting platforms. Cannot be opened or edited in Excel or text editors meaningfully.",
        "import_compatible": ["QuickBooks Desktop", "GnuCash", "Microsoft Money", "Moneydance", "FreeAgent"],
        "technical_notes": "BankScan AI generates OFX 2.2 compliant files with proper BANKACCTFROM headers, STMTTRN transaction records, and FITID unique identifiers for each transaction to prevent duplicates on re-import.",
        "unique_advantages": "Accounting software treats OFX imports like a native bank feed — transactions appear in the bank reconciliation screen ready for matching, rather than requiring manual column mapping.",
    },
    "qif": {
        "name": "QIF",
        "extension": ".qif",
        "icon": "file",
        "description": "Quicken Interchange Format (.qif) is a legacy but widely supported format originally created by Intuit for Quicken, now accepted by many financial applications.",
        "best_for": "Users of Quicken, older QuickBooks Desktop versions, and legacy financial software that accepts QIF but not OFX or CSV",
        "limitations": "No standardised date format (varies by locale). Does not support unique transaction IDs, so re-importing can create duplicates. Being phased out in favour of OFX and QFX.",
        "import_compatible": ["Quicken", "QuickBooks Desktop (older versions)", "GnuCash", "MoneyDance", "Banktivity"],
        "technical_notes": "BankScan AI generates QIF files with the correct date format for your locale (D field), payee names (P field), amounts (T field), and category hints (L field) where identifiable.",
        "unique_advantages": "Maximum backward compatibility with older financial software. Some users with legacy Quicken setups spanning decades rely exclusively on QIF for data continuity.",
    },
    "qbo": {
        "name": "QBO",
        "extension": ".qbo",
        "icon": "file",
        "description": "QuickBooks Web Connect (.qbo) is a QuickBooks-specific format that provides the smoothest possible import experience into QuickBooks Desktop and Online.",
        "best_for": "QuickBooks Desktop and QuickBooks Online users who want the cleanest import with automatic bank account matching and transaction categorisation suggestions",
        "limitations": "Only works with QuickBooks products. Cannot be opened in Excel or other software. Requires matching to an existing bank account in QuickBooks during import.",
        "import_compatible": ["QuickBooks Desktop", "QuickBooks Online", "QuickBooks Self-Employed"],
        "technical_notes": "BankScan AI generates QBO files with proper INTU.BID bank identifiers, unique FITID transaction IDs to prevent duplicates, and correctly signed amounts (negative for debits, positive for credits).",
        "unique_advantages": "QuickBooks recognises QBO files natively — double-click to import. Transactions land directly in the bank reconciliation queue with payee names and amounts pre-populated.",
    },
}

# ---------------------------------------------------------------------------
# Accounting software for integration pages
# ---------------------------------------------------------------------------
ACCOUNTING_SOFTWARE = {
    "xero": {
        "name": "Xero",
        "full_name": "Xero Accounting",
        "import_format": "CSV",
        "description": "Xero is the UK's most popular cloud accounting software for small businesses and accountants.",
        "import_notes": "BankScan AI produces Excel and CSV files formatted for direct import into Xero's bank statement upload feature. Columns match Xero's expected date, description, and amount format.",
        "import_steps": "In Xero, go to Accounting > Bank accounts > select your account > Import a Statement. Choose the CSV file from BankScan AI. Xero auto-maps the Date, Description, and Amount columns.",
        "date_format_required": "DD/MM/YYYY or YYYY-MM-DD",
        "column_mapping": "Date, Description, Amount (single column, negative for debits) — or Date, Description, Debit, Credit as separate columns",
        "common_import_errors": "Most common error is date format mismatch — Xero rejects MM/DD/YYYY dates. Also watch for CSV encoding issues with special characters in payee names.",
        "market_position": "Market leader in UK cloud accounting with over 1 million subscribers. Preferred by accountancy practices for Making Tax Digital compliance.",
        "unique_integration": "Xero's bank feed rules can auto-categorise imported transactions, so once you've set up rules, future imports from the same bank are largely automated.",
    },
    "quickbooks": {
        "name": "QuickBooks",
        "full_name": "QuickBooks Online",
        "import_format": "CSV",
        "description": "QuickBooks Online is widely used by UK small businesses and their accountants for invoicing, expenses, and VAT.",
        "import_notes": "Export your converted bank statement as CSV and import directly into QuickBooks via Banking > Upload transactions. BankScan AI formats the columns correctly.",
        "import_steps": "In QuickBooks Online, go to Banking > select your account > Link account > Upload from file. Select the CSV from BankScan AI, map the columns (Date, Description, Amount), and import.",
        "date_format_required": "DD/MM/YYYY for UK accounts, MM/DD/YYYY for US accounts",
        "column_mapping": "Date, Description, Amount — QuickBooks expects a single amount column where negative values are money out and positive values are money in",
        "common_import_errors": "QuickBooks rejects files with more than 350 rows per import — split large statements. Also fails if the Amount column contains currency symbols or commas.",
        "market_position": "Second most popular cloud accounting platform in the UK after Xero, dominant in the US market. Strong integration ecosystem with 750+ third-party apps.",
        "unique_integration": "QuickBooks' receipt matching feature can automatically pair imported bank transactions with receipt photos, creating a complete audit trail.",
    },
    "sage": {
        "name": "Sage",
        "full_name": "Sage Business Cloud Accounting",
        "import_format": "CSV",
        "description": "Sage is one of the UK's longest-established accounting software providers, used by thousands of SMEs.",
        "import_notes": "BankScan AI's CSV output is compatible with Sage's bank statement import. The date format, transaction descriptions, and amount columns are mapped automatically.",
        "import_steps": "In Sage Accounting, go to Banking > select your bank account > Import statement. Upload the CSV, confirm column mapping (Date, Details, Paid in, Paid out), and import.",
        "date_format_required": "DD/MM/YYYY",
        "column_mapping": "Date, Details/Reference, Paid in, Paid out — Sage requires separate debit and credit columns rather than a single signed amount",
        "common_import_errors": "Sage requires separate Paid in/Paid out columns — a single Amount column will fail. Also rejects imports where the date column contains time stamps.",
        "market_position": "UK heritage brand with 30+ years in accounting software. Sage 50 Desktop remains dominant in mid-market, while Sage Accounting targets cloud-first small businesses.",
        "unique_integration": "Sage's bank reconciliation remembers previous matching rules, so repeat transactions from the same payee are auto-suggested for the same nominal code.",
    },
    "freeagent": {
        "name": "FreeAgent",
        "full_name": "FreeAgent",
        "import_format": "CSV / OFX",
        "description": "FreeAgent is popular with UK freelancers and sole traders, especially those with NatWest, RBS, or Mettle accounts.",
        "import_notes": "Convert your bank statement PDF with BankScan AI, then import the CSV into FreeAgent's bank statement upload. Dates and amounts are formatted to match FreeAgent's requirements.",
        "import_steps": "In FreeAgent, go to Banking > select your account > Import Bank Statement. Choose CSV or OFX format, upload the file, and FreeAgent will parse the transactions automatically.",
        "date_format_required": "DD/MM/YYYY",
        "column_mapping": "Date, Description, Amount (single column) — FreeAgent also accepts OFX files which skip the column mapping step entirely",
        "common_import_errors": "FreeAgent can struggle with CSV files that have extra header rows or summary rows at the bottom. Ensure only transaction data rows are included.",
        "market_position": "Free for NatWest, RBS, and Ulster Bank business customers. Popular among UK freelancers and contractors for its simple self-assessment tax return filing.",
        "unique_integration": "FreeAgent automatically estimates your tax bill in real-time as transactions are imported, giving sole traders an up-to-date picture of their tax liability.",
    },
    "kashflow": {
        "name": "KashFlow",
        "full_name": "KashFlow",
        "import_format": "CSV",
        "description": "KashFlow is a simple cloud accounting tool popular with UK small businesses who want straightforward bookkeeping.",
        "import_notes": "BankScan AI's CSV export works directly with KashFlow's bank statement import feature. Upload and match transactions in minutes.",
        "import_steps": "In KashFlow, go to Bank > Bank Accounts > select your account > Import Transactions. Upload the CSV from BankScan AI and confirm the column mapping.",
        "date_format_required": "DD/MM/YYYY",
        "column_mapping": "Date, Reference, Amount — KashFlow uses a single amount column where positive is money in and negative is money out",
        "common_import_errors": "KashFlow can reject imports if the CSV has blank rows between transactions or if the date column has inconsistent formatting across rows.",
        "market_position": "Owned by IRIS Software since 2013. Positioned as entry-level cloud accounting for UK micro-businesses who find Xero and QuickBooks too complex.",
        "unique_integration": "KashFlow's 'PayPal sync' feature combined with bank statement import gives a complete picture of both online and bank transactions in one view.",
    },
    "wave": {
        "name": "Wave",
        "full_name": "Wave Accounting",
        "import_format": "CSV",
        "description": "Wave is a free accounting platform popular with freelancers and micro-businesses in the UK.",
        "import_notes": "Convert your bank statement to CSV with BankScan AI, then use Wave's import feature to upload transactions. The format is fully compatible.",
        "import_steps": "In Wave, go to Banking > Connected Accounts > Upload a bank statement (CSV). Select the file, map columns to Date, Description, and Amount, then import.",
        "date_format_required": "YYYY-MM-DD or MM/DD/YYYY",
        "column_mapping": "Date, Description, Amount — Wave supports both single amount column and separate Income/Expense columns",
        "common_import_errors": "Wave's free tier has occasional processing delays on large files. Also, Wave uses MM/DD/YYYY by default which conflicts with UK DD/MM/YYYY — select the correct format during import.",
        "market_position": "Completely free with no paid tier for accounting features (monetised through payment processing). Popular with budget-conscious startups and side-hustlers.",
        "unique_integration": "Wave's invoicing is tightly coupled with bank imports — when you import a bank deposit, Wave suggests matching it to outstanding invoices automatically.",
    },
    "zoho-books": {
        "name": "Zoho Books",
        "full_name": "Zoho Books",
        "import_format": "CSV",
        "description": "Zoho Books is a cloud accounting solution that integrates with the wider Zoho suite, used by growing UK businesses.",
        "import_notes": "BankScan AI's CSV output imports directly into Zoho Books' banking module. Transaction dates, descriptions, and amounts are correctly formatted.",
        "import_steps": "In Zoho Books, go to Banking > select your account > Import Statement. Upload the CSV, map Date, Description, Withdrawal, and Deposit columns, then import.",
        "date_format_required": "DD/MM/YYYY or YYYY-MM-DD",
        "column_mapping": "Date, Description, Withdrawal, Deposit — Zoho Books requires separate columns for money in and money out",
        "common_import_errors": "Zoho Books requires separate Withdrawal and Deposit columns — a single Amount column will fail. Also rejects files larger than 5MB.",
        "market_position": "Part of the Zoho ecosystem (CRM, Projects, Invoice). Attractive for businesses already using Zoho products who want tight integration across their tech stack.",
        "unique_integration": "Zoho Books auto-categorises imported transactions using machine learning trained on your previous categorisation patterns.",
    },
    "freshbooks": {
        "name": "FreshBooks",
        "full_name": "FreshBooks",
        "import_format": "CSV / OFX",
        "description": "FreshBooks is popular with UK service businesses and freelancers for invoicing and expense tracking.",
        "import_notes": "Convert your bank statement PDF to CSV with BankScan AI, then import into FreshBooks to reconcile invoices and track expenses automatically.",
        "import_steps": "In FreshBooks, go to Banking > Add Account > Import Transactions from File. Upload your CSV or OFX file, map the columns, and import.",
        "date_format_required": "YYYY-MM-DD or MM/DD/YYYY",
        "column_mapping": "Date, Description, Amount — FreshBooks supports single amount column (negative for expenses) or separate Debit/Credit columns",
        "common_import_errors": "FreshBooks limits imports to 500 transactions per file. For longer periods, split into monthly files. OFX import is generally smoother than CSV.",
        "market_position": "Strong in the US and Canada, growing in the UK. Known for the best invoicing experience among small business accounting tools.",
        "unique_integration": "FreshBooks automatically matches imported bank transactions to sent invoices, marking them as paid and updating cash flow reports in real time.",
    },
    "clear-books": {
        "name": "Clear Books",
        "full_name": "Clear Books",
        "import_format": "CSV",
        "description": "Clear Books is a UK-built cloud accounting platform designed specifically for small businesses and their accountants.",
        "import_notes": "BankScan AI's CSV output is formatted for direct import into Clear Books' bank reconciliation feature.",
        "import_steps": "In Clear Books, go to Money > Bank Accounts > select your account > Import. Upload the CSV and map Date, Description, Money in, and Money out columns.",
        "date_format_required": "DD/MM/YYYY",
        "column_mapping": "Date, Description, Money in, Money out — Clear Books uses separate columns for credits and debits",
        "common_import_errors": "Clear Books requires header row labels to exactly match expected names. Rename columns to Date, Description, Money in, Money out if import fails.",
        "market_position": "UK-only cloud accounting platform. Smaller market share but loyal user base among small businesses who prefer a UK-built, UK-supported solution.",
        "unique_integration": "Clear Books has built-in Corporation Tax estimation that updates as bank transactions are imported, giving directors a live view of their CT liability.",
    },
    "iris": {
        "name": "IRIS",
        "full_name": "IRIS Accountancy Suite",
        "import_format": "CSV",
        "description": "IRIS is used by thousands of UK accountancy practices for compliance, tax, and bookkeeping.",
        "import_notes": "Convert client bank statements to CSV with BankScan AI, then import into IRIS for fast bank reconciliation across your client portfolio.",
        "import_steps": "In IRIS Accountancy Suite, open the client file, go to Bank > Import Transactions. Select the CSV file, map columns to Date, Narrative, Debit, Credit, and import.",
        "date_format_required": "DD/MM/YYYY",
        "column_mapping": "Date, Narrative, Debit, Credit — IRIS uses Narrative instead of Description and requires separate Debit/Credit columns",
        "common_import_errors": "IRIS is strict about column naming — use 'Narrative' not 'Description'. Also requires the CSV to not contain any currency symbols in amount columns.",
        "market_position": "Dominant in UK accountancy practices — used by 6 of the top 10 UK firms. IRIS is the professional's choice for multi-client practice management.",
        "unique_integration": "IRIS links bank transactions directly to tax computations, so imported statements feed into both bookkeeping and tax return preparation simultaneously.",
    },
    "dext": {
        "name": "Dext",
        "full_name": "Dext (formerly Receipt Bank)",
        "import_format": "CSV",
        "description": "Dext automates data extraction from receipts and invoices for accountants and bookkeepers across the UK.",
        "import_notes": "BankScan AI complements Dext by converting bank statement PDFs to CSV. Import into Dext or directly into your accounting package for reconciliation.",
        "import_steps": "BankScan AI and Dext work together in your workflow: use BankScan AI for bank statement PDFs, Dext for receipts and invoices, then import both into your accounting software.",
        "date_format_required": "DD/MM/YYYY (for onward import to accounting software)",
        "column_mapping": "N/A — Dext is not a direct import target. BankScan AI outputs feed into the same accounting software that Dext publishes to (Xero, QBO, Sage).",
        "common_import_errors": "No direct import errors — Dext and BankScan AI are complementary tools. Ensure both use the same accounting software integration for consistent reconciliation.",
        "market_position": "Market leader in receipt and invoice data extraction for accountants. Over 1 million users worldwide. Acquired by Dext Group (formerly Xavier Analytics).",
        "unique_integration": "Dext's bank reconciliation view can show imported bank transactions alongside extracted receipt data, making it easy to match spend to supporting documents.",
    },
    "taxcalc": {
        "name": "TaxCalc",
        "full_name": "TaxCalc",
        "import_format": "CSV",
        "description": "TaxCalc is a UK tax return and accounts production software used by accountants and individuals for self-assessment and company filings.",
        "import_notes": "Convert bank statements to CSV with BankScan AI and use the data to prepare self-assessment, partnership, and corporation tax returns in TaxCalc.",
        "import_steps": "TaxCalc doesn't have direct bank import — use BankScan AI to convert PDFs to CSV, then reference the spreadsheet data when completing income and expense boxes in TaxCalc.",
        "date_format_required": "DD/MM/YYYY (for reference; no direct import)",
        "column_mapping": "N/A — TaxCalc is tax compliance software, not bookkeeping. Use BankScan AI's Excel output as a working paper alongside TaxCalc.",
        "common_import_errors": "No direct import — common mistake is trying to import CSV into TaxCalc. Instead, use the spreadsheet as a reference document for completing tax return boxes.",
        "market_position": "Top 3 UK tax software alongside IRIS and Taxfiler. Known for excellent HMRC integration and reliably early MTD updates each tax year.",
        "unique_integration": "TaxCalc's SimpleStep mode walks users through each tax return box — having BankScan AI's categorised bank data open alongside makes completion significantly faster.",
    },
    "taxfiler": {
        "name": "Taxfiler",
        "full_name": "Taxfiler",
        "import_format": "CSV",
        "description": "Taxfiler is a cloud-based UK tax compliance platform for accountancy practices, covering personal tax, CT600, and accounts production.",
        "import_notes": "Convert client bank statements to structured CSV files with BankScan AI, then use the data for income verification and expense categorisation in Taxfiler.",
        "import_steps": "Taxfiler doesn't import bank statements directly. Convert with BankScan AI, then use the CSV data to verify income figures and categorise expenses within Taxfiler's accounts production module.",
        "date_format_required": "DD/MM/YYYY (for reference)",
        "column_mapping": "N/A — Taxfiler is a tax compliance tool. BankScan AI CSV output serves as supporting documentation and working papers.",
        "common_import_errors": "No direct import. Use BankScan AI output as working papers — sort by category to identify employment income, self-employment turnover, rental income, and capital gains.",
        "market_position": "Cloud-native competitor to TaxCalc and IRIS for UK accountancy practices. Strong on CT600 and accounts production with good Companies House filing integration.",
        "unique_integration": "Taxfiler's accounts production can reference categorised bank data from BankScan AI spreadsheets to auto-populate trial balance figures.",
    },
    "pandle": {
        "name": "Pandle",
        "full_name": "Pandle",
        "import_format": "CSV / QIF",
        "description": "Pandle is a free bookkeeping software for UK sole traders and small businesses, with automatic bank feeds and simple invoicing.",
        "import_notes": "BankScan AI's CSV output imports directly into Pandle's bank statement upload feature. Perfect for sole traders who receive statements from clients or banks without direct feeds.",
        "import_steps": "In Pandle, go to Banking > select your account > Import Transactions. Upload the CSV or QIF file from BankScan AI. Pandle auto-maps standard column formats.",
        "date_format_required": "DD/MM/YYYY",
        "column_mapping": "Date, Description, Money In, Money Out — Pandle requires separate columns for credits and debits",
        "common_import_errors": "Pandle's free tier limits bank feed connections — manual CSV import via BankScan AI is the workaround for banks without direct Pandle feeds.",
        "market_position": "Free tier makes it popular with UK sole traders just starting out. Simple interface with no accounting jargon — designed for non-accountants.",
        "unique_integration": "Pandle's self-assessment feature pulls data from imported bank transactions to estimate your tax bill and generate SA100 figures automatically.",
    },
    "coconut": {
        "name": "Coconut",
        "full_name": "Coconut",
        "import_format": "CSV",
        "description": "Coconut is a UK business banking and accounting app for freelancers and sole traders that combines banking with bookkeeping.",
        "import_notes": "Convert statements from other bank accounts to CSV with BankScan AI and import them into Coconut to get a complete picture of your business finances.",
        "import_steps": "In Coconut, go to Transactions > Import. Upload the CSV from BankScan AI to bring in transactions from external bank accounts alongside your Coconut banking data.",
        "date_format_required": "DD/MM/YYYY",
        "column_mapping": "Date, Description, Amount — Coconut accepts a single amount column with positive for income and negative for expenses",
        "common_import_errors": "Coconut is primarily a banking app — the import feature is designed for bringing in transactions from non-Coconut accounts. Large file imports may time out on mobile.",
        "market_position": "Niche player combining business banking with basic bookkeeping. Targets freelancers who want banking and accounting in one app without managing separate tools.",
        "unique_integration": "Coconut auto-categorises imported transactions by comparing them against your existing Coconut transaction categories, learning your categorisation preferences over time.",
    },
    # --- US Accounting Software ---
    "turbotax": {
        "name": "TurboTax",
        "full_name": "Intuit TurboTax",
        "import_format": "CSV",
        "description": "TurboTax is America's most popular tax preparation software, used by millions of individual and business filers.",
        "import_notes": "BankScan AI converts bank statement PDFs to organized CSV files that help you identify deductible expenses and verify income during TurboTax preparation.",
        "import_steps": "TurboTax doesn't import bank CSVs directly. Use BankScan AI to convert PDFs to Excel, then sort transactions by category to identify deductible expenses for manual entry into TurboTax.",
        "date_format_required": "MM/DD/YYYY (for reference)",
        "column_mapping": "N/A — TurboTax is tax filing software. Use BankScan AI output as a working reference to complete income and deduction sections.",
        "common_import_errors": "No direct import. Common mistake: trying to upload CSV into TurboTax. Instead, use the spreadsheet to verify W-2 income, identify 1099 payments, and find deductible expenses.",
        "market_position": "Dominant US consumer tax software with 40+ million users. Ranges from Free Edition to Self-Employed tier covering Schedule C, E, and K-1 filers.",
        "unique_integration": "TurboTax's expense finder feature works alongside BankScan AI data — cross-reference your converted statements with TurboTax's deduction suggestions for maximum tax savings.",
    },
    "gusto": {
        "name": "Gusto",
        "full_name": "Gusto Payroll & HR",
        "import_format": "CSV",
        "description": "Gusto is a popular US payroll and HR platform for small businesses, handling payroll tax filing, benefits, and compliance.",
        "import_notes": "Convert bank statements to CSV with BankScan AI to reconcile payroll debits, tax payments, and benefits deductions against Gusto payroll reports.",
        "import_steps": "Gusto doesn't import bank statements. Use BankScan AI to convert your statements to Excel, then filter for payroll-related transactions to reconcile against Gusto's payroll reports.",
        "date_format_required": "MM/DD/YYYY (for reference)",
        "column_mapping": "N/A — Gusto is payroll software. Use BankScan AI output to verify payroll debits, tax deposits, and benefits payments match Gusto records.",
        "common_import_errors": "No direct import. Use BankScan AI data to identify and verify payroll tax deposits (941, FUTA, SUTA) appearing on your bank statement against Gusto's tax filing records.",
        "market_position": "Leading US payroll platform for small businesses with 300,000+ customers. Known for easy setup and excellent employee onboarding experience.",
        "unique_integration": "Reconcile Gusto payroll runs against bank statements by matching BankScan AI's extracted debit amounts to Gusto's payroll summary reports — catches timing differences and failed ACH transfers.",
    },
    "netsuite": {
        "name": "NetSuite",
        "full_name": "Oracle NetSuite",
        "import_format": "CSV",
        "description": "NetSuite is a leading cloud ERP used by mid-market and enterprise businesses in the US for financial management, CRM, and operations.",
        "import_notes": "BankScan AI's CSV output can be mapped to NetSuite's bank statement import format, enabling fast reconciliation for businesses without direct bank feeds.",
        "import_steps": "In NetSuite, go to Transactions > Bank > Import Bank Statement. Upload the CSV, map fields to Date, Description, Amount, and select the target bank account for reconciliation.",
        "date_format_required": "MM/DD/YYYY or YYYY-MM-DD",
        "column_mapping": "Date, Description, Amount, Check Number (optional) — NetSuite supports customizable column mapping during import wizard",
        "common_import_errors": "NetSuite's import wizard requires matching to an existing bank account. Multi-currency transactions need the currency code column. Duplicate detection uses date+amount, so identical transactions may need manual review.",
        "market_position": "Enterprise-grade ERP with 37,000+ organizations. Premium pricing starts at $999/month. The choice for scaling businesses that outgrow QuickBooks or Xero.",
        "unique_integration": "NetSuite's SuiteAnalytics can run custom reports on imported bank data, combining it with CRM, inventory, and project data for comprehensive business intelligence.",
    },
    "bench": {
        "name": "Bench",
        "full_name": "Bench Accounting",
        "import_format": "CSV",
        "description": "Bench provides bookkeeping services with proprietary software for US small businesses, combining human bookkeepers with technology.",
        "import_notes": "Convert bank statements to CSV with BankScan AI for Bench-compatible import. Useful when bank feeds are unavailable or historical statements need processing.",
        "import_steps": "Share BankScan AI's converted CSV files with your Bench bookkeeper via the Bench portal. They import the data into your Bench account for categorisation and reconciliation.",
        "date_format_required": "MM/DD/YYYY",
        "column_mapping": "Date, Description, Amount — Bench bookkeepers handle column mapping during their review process",
        "common_import_errors": "Bench's human bookkeepers handle most import issues. Main challenge is when bank feeds disconnect — BankScan AI serves as the backup data source for gap periods.",
        "market_position": "Unique hybrid model combining software with dedicated human bookkeepers. Popular with US small business owners who want hands-off bookkeeping without hiring an in-house accountant.",
        "unique_integration": "Bench bookkeepers can use BankScan AI's pre-structured data to speed up categorisation, reducing the typical 5-day turnaround to 1-2 days for historical catch-up bookkeeping.",
    },
    "bill-com": {
        "name": "Bill.com",
        "full_name": "Bill.com (BILL)",
        "import_format": "CSV",
        "description": "Bill.com automates accounts payable and receivable for US businesses, integrating with QuickBooks, Xero, and NetSuite.",
        "import_notes": "BankScan AI converts bank statements to structured CSV files for reconciling Bill.com payments against bank transactions, ensuring AP/AR accuracy.",
        "import_steps": "Bill.com doesn't import bank statements directly. Use BankScan AI CSV output to reconcile Bill.com payment records against actual bank debits and credits in your accounting software.",
        "date_format_required": "MM/DD/YYYY (for reference)",
        "column_mapping": "N/A — Bill.com is AP/AR software. Use BankScan AI data to verify that Bill.com-initiated payments cleared the bank correctly.",
        "common_import_errors": "No direct import. Common reconciliation issue: Bill.com batches multiple payments into single ACH debits — use BankScan AI's date and amount data to match batch totals.",
        "market_position": "Leading US AP/AR automation platform with 460,000+ customers. Essential tool for mid-market businesses processing high volumes of vendor payments and customer invoices.",
        "unique_integration": "Cross-reference BankScan AI bank data with Bill.com payment records to catch failed payments, duplicate charges, and timing differences between payment initiation and bank clearing.",
    },
}

# ---------------------------------------------------------------------------
# Use cases for bank statement conversion
# ---------------------------------------------------------------------------
USE_CASES = {
    "mortgage-application": {
        "name": "Mortgage Application",
        "description": "Convert bank statements for mortgage applications and affordability assessments",
        "pain_point": "Mortgage lenders require 3-6 months of bank statements to verify income and assess affordability. Manually organising these statements is time-consuming for both applicants and brokers.",
        "benefit": "Convert your bank statements to clean Excel spreadsheets that clearly show income, regular outgoings, and spending patterns — exactly what mortgage lenders need to see.",
        "keywords": "bank statement for mortgage, mortgage affordability statement, mortgage application bank statement converter",
        "typical_timeframe": "3-6 months of statements, though some lenders request 12 months for self-employed applicants",
        "required_by": "Mortgage lender or broker, acting on behalf of the underwriting team",
        "key_data_points": "Monthly income deposits, regular outgoings (rent, bills, loans), average balance, gambling transactions, overdraft usage, buy-now-pay-later payments",
        "formatting_requirements": "Chronological with income highlighted, ideally with monthly summaries showing total credits, total debits, and closing balance per month",
        "deadline_pressure": "Mortgage offers typically expire in 3-6 months; brokers often need documents within 48 hours to secure a rate lock",
        "legal_basis": "FCA MCOB (Mortgages and Home Finance: Conduct of Business) rules on affordability assessment and responsible lending",
    },
    "visa-application": {
        "name": "Visa Application",
        "description": "Prepare bank statements for UK visa and immigration applications",
        "pain_point": "UK visa applications (Tier 1, Tier 2, spouse, student) require bank statements proving financial capability. The Home Office needs clear, organised financial evidence.",
        "benefit": "Convert your bank statements to structured Excel spreadsheets that clearly show your balance history, income sources, and regular savings — formatted for visa evidence bundles.",
        "keywords": "bank statement for visa, UK visa bank statement, immigration financial evidence, spouse visa bank statement",
        "typical_timeframe": "6 months for most visa categories; 12 months for Innovator Founder and some ILR applications",
        "required_by": "UK Visas and Immigration (UKVI), part of the Home Office, reviewed by Entry Clearance Officers or caseworkers",
        "key_data_points": "Daily closing balance (must stay above maintenance threshold), source of funds, large deposits with explanations, evidence of savings not being temporarily moved in",
        "formatting_requirements": "Statements must show applicant name, account number, bank name, and every page dated; balance must be clearly visible for each day the maintenance period is assessed",
        "deadline_pressure": "Statements must cover the period ending no more than 31 days before the visa application date; delays mean re-obtaining fresh statements",
        "legal_basis": "Immigration Rules Appendix Finance and Appendix FM-SE; Home Office financial requirement guidance under the Points-Based System",
    },
    "tax-return": {
        "name": "Self-Assessment Tax Return",
        "description": "Prepare bank statements for HMRC self-assessment tax returns",
        "pain_point": "Self-assessment requires reviewing a full year of bank transactions to identify income, allowable expenses, and taxable events. Doing this manually from PDFs takes hours.",
        "benefit": "Convert 12 months of bank statements to Excel in minutes. Filter, sort, and categorise transactions to identify income and allowable expenses for your SA100.",
        "keywords": "bank statement for tax return, self assessment bank statement, HMRC tax return statement converter",
        "typical_timeframe": "Full tax year (6 April to 5 April), so 12 months of statements",
        "required_by": "HMRC, for self-assessment filing; also your accountant or tax advisor preparing the return on your behalf",
        "key_data_points": "Self-employment income, rental income, interest earned, dividend receipts, allowable business expenses by category, capital gains proceeds",
        "formatting_requirements": "Transactions categorised by SA100 box number or trade income/expense type; annual totals per category for direct entry into tax return",
        "deadline_pressure": "Paper returns due 31 October, online returns due 31 January following the tax year; late filing triggers automatic penalties starting at 100 GBP",
        "legal_basis": "Taxes Management Act 1970 sections 8-12; HMRC record-keeping requirements under SA BK3 guidance",
    },
    "hmrc-investigation": {
        "name": "HMRC Investigation",
        "description": "Prepare bank statements for HMRC tax investigations and enquiries",
        "pain_point": "HMRC investigations can request years of bank statements. Converting these to analysable data is critical for responding quickly and accurately.",
        "benefit": "Batch-convert multiple years of bank statements to structured spreadsheets for HMRC enquiry responses, voluntary disclosures, and tax investigation defence.",
        "keywords": "HMRC investigation bank statement, tax enquiry statement converter, HMRC compliance statement tool",
        "typical_timeframe": "Typically 3-6 years, but HMRC can request up to 20 years in cases of deliberate fraud or failure to notify",
        "required_by": "HMRC Compliance Caseworker or Fraud Investigation Service officer, via formal Information Notice under Schedule 36",
        "key_data_points": "All credits analysed against declared income, unexplained deposits, cash withdrawals, transfers between accounts, lifestyle spending inconsistent with declared earnings",
        "formatting_requirements": "Year-by-year analysis with credits and debits separated, unexplained credits flagged, cross-referenced against tax returns already filed",
        "deadline_pressure": "HMRC typically allows 30-60 days to respond to an Information Notice; failure to comply can result in daily penalties of 300 GBP or tax tribunal referral",
        "legal_basis": "Finance Act 2008 Schedule 36 (Information and Inspection Powers); Taxes Management Act 1970 section 9A (enquiry into self-assessment return)",
    },
    "divorce-proceedings": {
        "name": "Divorce Proceedings",
        "description": "Convert bank statements for financial disclosure in divorce cases",
        "pain_point": "Divorce financial disclosure (Form E) requires detailed bank statement analysis. Solicitors and clients need to review months of transactions to identify assets and spending.",
        "benefit": "Convert bank statements to searchable Excel spreadsheets for Form E preparation, asset tracing, and financial disclosure in divorce proceedings.",
        "keywords": "bank statement for divorce, Form E bank statement, financial disclosure statement converter, matrimonial finance tool",
        "typical_timeframe": "12 months minimum, often 2-3 years for asset tracing and establishing spending patterns during the marriage",
        "required_by": "Family court via Form E financial disclosure; opposing solicitors and forensic accountants may also review the data",
        "key_data_points": "Hidden income, undisclosed accounts, transfers to third parties, luxury spending, dissipation of assets, regular savings contributions, mortgage payments",
        "formatting_requirements": "Organised by account with running totals, suspicious transactions highlighted, summaries matching Form E sections (income, liabilities, expenditure)",
        "deadline_pressure": "Form E must be filed by the court deadline, typically 35 days after the directions order; late disclosure can result in adverse cost orders",
        "legal_basis": "Family Procedure Rules 2010 Part 9; duty of full and frank financial disclosure established in Livesey v Jenkins [1985]",
    },
    "probate": {
        "name": "Probate & Estate Administration",
        "description": "Convert bank statements for probate applications and estate administration",
        "pain_point": "Executors and solicitors handling probate need to review the deceased's bank statements to value the estate, identify debts, and distribute assets.",
        "benefit": "Convert the deceased's bank statements to Excel for fast estate valuation, identification of standing orders, direct debits, and outstanding payments.",
        "keywords": "bank statement for probate, estate administration statement tool, executor bank statement converter",
        "typical_timeframe": "Date of death statement plus 6-12 months prior to identify regular commitments, standing orders, and direct debits to cancel",
        "required_by": "HM Courts & Tribunals Service (for probate application), HMRC (for IHT400 inheritance tax form), and beneficiaries",
        "key_data_points": "Balance at date of death, standing orders and direct debits payable, regular income sources, any joint account contributions, gifts made in the 7 years before death",
        "formatting_requirements": "Date-of-death balance clearly stated, recurring payments listed separately for cancellation, all accounts of the deceased consolidated into one estate summary",
        "deadline_pressure": "IHT400 must be submitted within 12 months of death to avoid interest charges; probate grant cannot be issued until HMRC receives the IHT forms",
        "legal_basis": "Administration of Estates Act 1925; Inheritance Tax Act 1984 sections 4 and 171; Non-Contentious Probate Rules 1987",
    },
    "audit-preparation": {
        "name": "Audit Preparation",
        "description": "Prepare bank statements for statutory and internal audits",
        "pain_point": "Auditors need to verify bank balances and test transactions against financial statements. Getting bank data into a workable format is the first bottleneck.",
        "benefit": "Convert bank statements to structured Excel files for audit testing, balance verification, and transaction sampling. Save hours of data preparation.",
        "keywords": "bank statement for audit, audit preparation statement tool, statutory audit bank statement converter",
        "typical_timeframe": "Full financial year (12 months), plus confirmation of opening and closing balances for the period under audit",
        "required_by": "External statutory auditors (for Companies Act audits) or internal audit teams conducting periodic reviews",
        "key_data_points": "Year-end bank balance for bank confirmation, large or unusual transactions for substantive testing, unreconciled items, related party transactions",
        "formatting_requirements": "Transactions sorted chronologically with running balance, year-end closing balance matching the bank confirmation letter, amounts in audit sampling format",
        "deadline_pressure": "Statutory accounts must be filed within 9 months of year-end for private companies; audit fieldwork typically compressed into 2-4 weeks",
        "legal_basis": "Companies Act 2006 sections 475-539 (statutory audit requirements); ISA (UK) 500 on audit evidence and ISA (UK) 505 on external confirmations",
    },
    "business-loan": {
        "name": "Business Loan Application",
        "description": "Prepare bank statements for business loan and finance applications",
        "pain_point": "Lenders and finance brokers require 6-12 months of business bank statements to assess cash flow and creditworthiness. Disorganised PDFs slow down applications.",
        "benefit": "Convert your business bank statements to clean spreadsheets showing cash flow, revenue patterns, and regular commitments — accelerating your loan application.",
        "keywords": "bank statement for business loan, finance application statement, cash flow statement converter",
        "typical_timeframe": "6-12 months of business bank statements; asset finance may only need 3 months while commercial mortgages often require 24 months",
        "required_by": "Commercial lending team at the bank or alternative lender, often via a finance broker who packages the application",
        "key_data_points": "Monthly turnover, average daily balance, minimum balance, existing loan repayments, returned payments or bounced items, merchant card receipts",
        "formatting_requirements": "Monthly summary showing total deposits versus withdrawals, average balance calculation, cash flow trend visible at a glance for credit committee review",
        "deadline_pressure": "Commercial loan offers typically have a 30-90 day validity; brokers may need statements within days to meet funding deadlines or property completion dates",
        "legal_basis": "FCA Consumer Credit sourcebook (CONC) for regulated lending; Senior Managers and Certification Regime (SM&CR) for responsible lending decisions",
    },
    "company-accounts": {
        "name": "Annual Company Accounts",
        "description": "Prepare bank statements for Companies House annual accounts filing",
        "pain_point": "Preparing annual accounts for Companies House requires reconciling a full year of bank transactions. Many small companies still rely on PDF statements from their bank.",
        "benefit": "Convert your full year of bank statements to Excel for fast reconciliation, trial balance preparation, and Companies House filing.",
        "keywords": "bank statement for company accounts, Companies House statement converter, annual accounts bank reconciliation",
        "typical_timeframe": "Full financial year (12 months), aligned to the company's accounting reference date",
        "required_by": "Companies House (for annual filing), HMRC (for Corporation Tax return CT600), and the company's directors and shareholders",
        "key_data_points": "Opening and closing bank balances, total income received, categorised expenditure, inter-company transfers, director loan account movements, dividend payments",
        "formatting_requirements": "Full year reconciled to the nominal ledger, with month-end balances matching bank reconciliation statements, ready for trial balance extraction",
        "deadline_pressure": "Accounts must be filed within 9 months of the accounting year-end for private companies; late filing penalties start at 150 GBP and escalate to 1,500 GBP",
        "legal_basis": "Companies Act 2006 sections 394-397 (duty to prepare accounts) and sections 441-453 (filing requirements and penalties)",
    },
    "grant-application": {
        "name": "Grant Application",
        "description": "Prepare bank statements for grant funding applications",
        "pain_point": "Grant applications often require evidence of financial health, cash flow, and how previous funding was spent — typically shown through bank statements.",
        "benefit": "Convert your bank statements to clear spreadsheets that demonstrate financial health, spending patterns, and fund usage for grant applications.",
        "keywords": "bank statement for grant application, funding evidence statement tool, charity grant bank statement",
        "typical_timeframe": "3-12 months, depending on the funder; Arts Council and Innovate UK typically require 3 months, while National Lottery may require a full year",
        "required_by": "Grant-making body (e.g. Arts Council England, Innovate UK, National Lottery, local authority, or charitable trust) as part of the eligibility and due diligence assessment",
        "key_data_points": "Current unrestricted reserves, evidence of matched funding, previous grant expenditure trail, operational costs demonstrating going-concern viability",
        "formatting_requirements": "Summary of reserves and cash position, with previous grant funds ring-fenced or traceable, demonstrating the organisation can manage public money responsibly",
        "deadline_pressure": "Grant rounds have fixed closing dates; applications without complete financial evidence are typically rejected outright rather than given extensions",
        "legal_basis": "Charity Commission CC25 guidance on charity accounts; specific funder terms and conditions governing financial evidence requirements",
    },
    "rent-application": {
        "name": "Rental Application",
        "description": "Prepare bank statements for tenant referencing and rental applications",
        "pain_point": "Letting agents and referencing agencies require 3 months of bank statements to verify tenant income and affordability. Tenants need to provide clear, readable statements.",
        "benefit": "Convert your bank statements to Excel format for clean, professional-looking financial evidence that speeds up your rental application.",
        "keywords": "bank statement for renting, tenant referencing statement, rental application bank statement converter",
        "typical_timeframe": "3 months of recent statements, covering the period immediately before the application",
        "required_by": "Letting agent or landlord, often processed through a referencing agency such as Goodlord, OpenRent, or HomeLet",
        "key_data_points": "Regular salary or income deposits, rent payments to current landlord, affordability ratio (rent typically should not exceed 30-40% of gross income), no persistent overdraft usage",
        "formatting_requirements": "Clear income identification with employer name visible, current rent payments highlighted, no redactions that would raise suspicion with the referencing agency",
        "deadline_pressure": "Rental properties move fast, especially in London; referencing must often be completed within 24-48 hours to secure the property before another applicant",
        "legal_basis": "Tenant Fees Act 2019 (limits what agents can charge for referencing); Right to Rent checks under Immigration Act 2014; GDPR for handling tenant financial data",
    },
    "expense-report": {
        "name": "Expense Reporting",
        "description": "Convert bank statements for business expense reports and reimbursement",
        "pain_point": "Employees and business owners need to extract business expenses from personal or corporate bank statements for reimbursement claims and expense reporting.",
        "benefit": "Convert your bank statement to Excel, then quickly filter and categorise business expenses for reimbursement claims, P11D reporting, or management accounts.",
        "keywords": "bank statement for expenses, expense report statement tool, P11D bank statement converter, business expense tracker",
        "typical_timeframe": "Monthly or quarterly, depending on the company's expense policy; P11D reporting covers the full tax year",
        "required_by": "Employer's finance or accounts payable team for reimbursement; HMRC for P11D benefits-in-kind reporting at year-end",
        "key_data_points": "Business travel costs, client entertainment, subsistence, mileage-related fuel purchases, professional subscriptions, home office costs for remote workers",
        "formatting_requirements": "Expenses categorised by HMRC-allowable type, with date, payee, amount, and business purpose; VAT amount separated where applicable for reclaim",
        "deadline_pressure": "Monthly expense claims typically due within 30 days of the expense; P11D filing deadline is 6 July following the tax year",
        "legal_basis": "ITEPA 2003 Part 5 (employment income: expenses); HMRC Booklet 490 on employee travel and subsistence; P11D and P11D(b) reporting requirements",
    },
    "bank-reconciliation": {
        "name": "Bank Reconciliation",
        "description": "Convert bank statements for monthly bank reconciliation",
        "pain_point": "Monthly bank reconciliation requires matching every bank transaction against your accounting records. Working from PDF statements makes this slow and error-prone.",
        "benefit": "Convert bank statement PDFs to Excel for fast side-by-side reconciliation with your accounting records. Sort by date, filter by amount, and spot discrepancies instantly.",
        "keywords": "bank reconciliation tool, bank statement reconciliation, monthly reconciliation statement converter",
        "typical_timeframe": "Monthly, covering one calendar month per reconciliation cycle; year-end reconciliation covers the full 12-month period",
        "required_by": "Internal finance team, bookkeeper, or accountant responsible for maintaining accurate accounting records and detecting errors or fraud",
        "key_data_points": "Every transaction with date, description, and amount; opening and closing balances; uncleared cheques, pending deposits, and timing differences",
        "formatting_requirements": "Transactions in exact chronological order with running balance, matching the bank's own running total; amounts must reconcile to the penny against the nominal ledger",
        "deadline_pressure": "Best practice is to reconcile within 5 working days of month-end; delays compound and make year-end reconciliation significantly harder",
        "legal_basis": "No specific statute, but FRS 102 and Companies Act 2006 require accurate financial records; auditors test bank reconciliations as a core audit procedure under ISA 500",
    },
    "management-accounts": {
        "name": "Management Accounts",
        "description": "Prepare bank statements for monthly management accounts and reporting",
        "pain_point": "Preparing monthly management accounts requires categorising all bank transactions by cost centre, project, or department. PDF statements make this analysis difficult.",
        "benefit": "Convert bank statements to Excel spreadsheets for fast categorisation, pivot table analysis, and management reporting. Save hours of manual data extraction.",
        "keywords": "bank statement for management accounts, management reporting statement tool, monthly accounts bank statement",
        "typical_timeframe": "Monthly, covering the previous calendar month; some businesses also require quarterly board packs with 3-month rolling data",
        "required_by": "Company directors, board of directors, or business owners for internal decision-making; also used by fractional FDs and management accountants",
        "key_data_points": "Revenue by stream, cost of sales, overheads by category, payroll costs, gross and net profit margins, cash position versus budget, variance analysis",
        "formatting_requirements": "Transactions categorised by nominal code or cost centre, with pivot-table-ready structure for departmental P&L analysis and budget-versus-actual comparison",
        "deadline_pressure": "Management accounts are typically expected within 10-15 working days of month-end to be useful for decision-making; stale data loses its management value",
        "legal_basis": "No statutory requirement for management accounts, but directors have a fiduciary duty under Companies Act 2006 section 172 to make informed decisions about the company",
    },
    "vat-return": {
        "name": "VAT Return",
        "description": "Prepare bank statements for quarterly VAT return filing",
        "pain_point": "Quarterly VAT returns require matching bank transactions to sales and purchase invoices. Working from PDF statements adds unnecessary time to an already tight deadline.",
        "benefit": "Convert bank statements to Excel to quickly identify VAT-bearing transactions, match against invoices, and prepare your VAT return accurately.",
        "keywords": "bank statement for VAT return, VAT reconciliation statement tool, Making Tax Digital bank statement",
        "typical_timeframe": "3 months per VAT quarter (standard scheme); 12 months for annual accounting scheme users",
        "required_by": "HMRC, via Making Tax Digital (MTD) compatible software submission; also reviewed by the business's accountant or bookkeeper",
        "key_data_points": "VAT-inclusive sales and purchases, zero-rated transactions, exempt supplies, input VAT on expenses, output VAT on sales, EC acquisitions, reverse charge transactions",
        "formatting_requirements": "Transactions split into VAT-bearing and non-VAT categories, with net and VAT amounts separated; must reconcile to Box 6 (total sales) and Box 7 (total purchases)",
        "deadline_pressure": "VAT returns due 1 month and 7 days after the end of the VAT quarter; late submission triggers a surcharge of up to 15% of VAT owed under the default surcharge regime",
        "legal_basis": "Value Added Tax Act 1994; VAT Regulations 1995; Making Tax Digital for VAT regulations (The Value Added Tax (Amendment) Regulations 2018)",
    },
    "cash-flow-forecast": {
        "name": "Cash Flow Forecasting",
        "description": "Convert bank statements for cash flow analysis and forecasting",
        "pain_point": "Cash flow forecasting requires analysing historical bank transactions to predict future income and expenditure patterns. PDF statements can't be analysed programmatically.",
        "benefit": "Convert months of bank statements to Excel and use pivot tables, charts, and formulas to build accurate cash flow forecasts from real transaction data.",
        "keywords": "bank statement for cash flow, cash flow forecast statement tool, cash flow analysis bank statement",
        "typical_timeframe": "6-12 months of historical data to establish reliable patterns for forecasting 3-12 months ahead",
        "required_by": "Business owner, FD/CFO, or management accountant for internal planning; also requested by lenders and investors assessing business viability",
        "key_data_points": "Recurring income timing and amounts, seasonal revenue patterns, fixed versus variable costs, payment terms with major customers and suppliers, one-off items to exclude",
        "formatting_requirements": "Weekly or monthly cash flow buckets with receipts and payments separated, opening and closing cash position, cumulative cash flow trend line for visual analysis",
        "deadline_pressure": "Cash flow forecasts should be updated monthly at minimum; businesses approaching cash crunches may need weekly rolling forecasts to manage survival",
        "legal_basis": "Directors' duty to monitor solvency under Companies Act 2006 section 174 (duty of care) and Insolvency Act 1986 section 214 (wrongful trading provisions)",
    },
    "insurance-claim": {
        "name": "Insurance Claim",
        "description": "Prepare bank statements for insurance claims and loss evidence",
        "pain_point": "Insurance claims for business interruption, theft, or fraud require bank statements as evidence of financial loss. Insurers need clear, organised financial documentation.",
        "benefit": "Convert bank statements to structured Excel spreadsheets showing income patterns, loss periods, and transaction evidence for insurance claim submissions.",
        "keywords": "bank statement for insurance claim, business interruption evidence, insurance loss statement tool",
        "typical_timeframe": "12-24 months pre-loss for baseline comparison, plus the entire loss period; business interruption claims may need 3+ years of historical data",
        "required_by": "Insurance company claims adjuster or loss adjuster, often working alongside a forensic accountant who quantifies the financial loss",
        "key_data_points": "Pre-loss revenue baseline, post-loss revenue decline, increased cost of working, continuing fixed costs during interruption, any saved expenses or alternative revenue",
        "formatting_requirements": "Month-by-month comparison of pre-loss versus post-loss periods, with seasonal adjustments shown; clear separation of insured versus uninsured losses",
        "deadline_pressure": "Most policies require notification within 30 days and full claim submission within 6-12 months; delays give insurers grounds to question or deny the claim",
        "legal_basis": "Insurance Act 2015 (duty of fair presentation); policy-specific claims conditions; Financial Ombudsman Service rules on fair claims handling",
    },
    "investor-reporting": {
        "name": "Investor Reporting",
        "description": "Prepare bank statements for investor updates and due diligence",
        "pain_point": "Investors and VCs request bank statements during due diligence and ongoing reporting. Presenting raw PDF statements looks unprofessional and slows the process.",
        "benefit": "Convert your bank statements to clean Excel spreadsheets for professional investor reporting, burn rate analysis, and due diligence document preparation.",
        "keywords": "bank statement for investors, due diligence statement tool, investor reporting bank statement converter",
        "typical_timeframe": "12-24 months for due diligence; monthly or quarterly for ongoing investor reporting and board updates",
        "required_by": "Venture capital or private equity investors, angel investors, or their appointed due diligence advisors (typically Big 4 or corporate finance firms)",
        "key_data_points": "Monthly burn rate, cash runway in months, revenue growth trajectory, customer concentration risk, related party transactions, founder salary and expenses",
        "formatting_requirements": "Clean monthly summary with MRR/ARR reconciliation against bank deposits, burn rate trend, and cash runway calculation; presentation-ready for board decks",
        "deadline_pressure": "Due diligence typically runs 4-8 weeks with a tight data room deadline; ongoing investor reports are usually expected within 15 days of month-end",
        "legal_basis": "Companies Act 2006 section 431 (right of shareholders to inspect accounts); EIS/SEIS compliance requirements under ITA 2007 for tax-advantaged investors",
    },
    "debt-management": {
        "name": "Debt Management",
        "description": "Convert bank statements for debt management plans and IVA applications",
        "pain_point": "Debt management advisors and IVA supervisors need detailed analysis of a debtor's bank statements to assess income, essential spending, and disposable income.",
        "benefit": "Convert bank statements to Excel to quickly categorise income and expenditure, calculate disposable income, and prepare debt management or IVA proposals.",
        "keywords": "bank statement for debt management, IVA bank statement, debt advisor statement tool, income expenditure analysis",
        "typical_timeframe": "3-6 months of recent statements to establish a reliable income and expenditure pattern for the proposal",
        "required_by": "Insolvency practitioner (for IVAs), debt management company, or free debt advice service (StepChange, Citizens Advice, National Debtline)",
        "key_data_points": "Net income from all sources, essential living costs (housing, utilities, food, transport), non-essential spending to reduce, existing debt repayments, disposable income calculation",
        "formatting_requirements": "Income and expenditure categorised according to the Standard Financial Statement (SFS) format used across the UK debt advice sector",
        "deadline_pressure": "IVA proposals must be sent to creditors within 14 days of the nominee's report; debt management plans need quick setup to stop creditor action and interest accrual",
        "legal_basis": "Insolvency Act 1986 Part VIII (Individual Voluntary Arrangements); FCA CONC 8 (debt counselling, debt adjusting, and debt administration)",
    },
    "anti-money-laundering": {
        "name": "Anti-Money Laundering (AML)",
        "description": "Convert bank statements for AML checks and suspicious activity reporting",
        "pain_point": "AML compliance officers need to review bank statements to identify suspicious transactions, unusual patterns, and politically exposed person (PEP) activity.",
        "benefit": "Convert bank statements to searchable, sortable spreadsheets for systematic AML review, transaction pattern analysis, and suspicious activity report preparation.",
        "keywords": "bank statement for AML, anti money laundering statement tool, suspicious activity report bank statement, KYC statement converter",
        "typical_timeframe": "Ongoing monitoring typically reviews 3-12 months; enhanced due diligence on high-risk clients may require 2+ years of transaction history",
        "required_by": "Money Laundering Reporting Officer (MLRO) at the regulated firm, supervised by the FCA, HMRC, or relevant professional body (e.g. ICAEW, SRA)",
        "key_data_points": "Transactions inconsistent with known client profile, cash deposits near reporting thresholds (structuring), international transfers to high-risk jurisdictions, rapid movement of funds through accounts",
        "formatting_requirements": "Sortable by amount (descending) to identify large transactions, filterable by transaction type, with counterparty names searchable for sanctions list cross-referencing",
        "deadline_pressure": "Suspicious Activity Reports (SARs) should be filed with the NCA promptly; consent SARs require a response within 7 working days plus a 31-day moratorium period",
        "legal_basis": "Proceeds of Crime Act 2002 sections 327-329; Money Laundering Regulations 2017 (SI 2017/692); FCA Financial Crime Guide (FCG)",
    },
    # --- US Use Cases ---
    "irs-audit": {
        "name": "IRS Audit",
        "description": "Prepare bank statements for IRS audit examination and tax dispute resolution",
        "pain_point": "IRS audits require detailed bank statement analysis to verify reported income, substantiate deductions, and respond to Information Document Requests (IDRs) within tight deadlines.",
        "benefit": "Batch-convert years of bank statements to searchable Excel spreadsheets for rapid IRS audit response, income verification, and deduction substantiation.",
        "keywords": "bank statement for IRS audit, tax audit bank statement, IRS examination statement converter, IDR response tool",
        "typical_timeframe": "3 years under normal statute of limitations; 6 years if substantial understatement suspected; unlimited for fraud or unfiled returns",
        "required_by": "IRS Revenue Agent or Tax Compliance Officer conducting the examination, via Information Document Request (IDR)",
        "key_data_points": "Total deposits analysis (IRS bank deposit method), non-taxable deposits to exclude (transfers, loans, gifts), unreported income identification, deduction substantiation",
        "formatting_requirements": "All deposits listed and categorized as taxable or non-taxable with supporting explanations; total deposits reconciled to reported gross income on the return",
        "deadline_pressure": "IDR responses typically due within 10-15 business days; statute of limitations expiry creates urgency for both taxpayer and IRS to resolve the audit",
        "legal_basis": "Internal Revenue Code section 7602 (examination authority); IRC section 6501 (statute of limitations on assessment); Revenue Procedure 2005-32 (IDR procedures)",
    },
    "sba-loan": {
        "name": "SBA Loan Application",
        "description": "Prepare bank statements for SBA loan applications and business financing",
        "pain_point": "SBA lenders require 3-12 months of business bank statements to verify revenue, assess cash flow, and determine loan eligibility. Disorganized PDFs delay the application process.",
        "benefit": "Convert business bank statements to clean spreadsheets showing monthly revenue, average daily balance, and cash flow patterns — exactly what SBA lenders need to see.",
        "keywords": "bank statement for SBA loan, SBA 7a bank statement, business loan bank statement converter, SBA financing",
        "typical_timeframe": "3 months for SBA Express loans; 12 months for standard SBA 7(a) and 504 loans; some lenders request 24 months for startups",
        "required_by": "SBA-approved lender (bank or credit union), with the SBA guaranteeing a portion of the loan; loan packager or broker may also request the statements",
        "key_data_points": "Monthly gross deposits, average daily balance, NSF/overdraft occurrences, existing business debt service payments, owner draws versus reinvestment",
        "formatting_requirements": "Monthly deposit summary with daily balance trend, showing the Debt Service Coverage Ratio (DSCR) can be calculated from actual cash flow data",
        "deadline_pressure": "SBA loan approvals can take 30-90 days; stale financial documents older than 120 days are typically rejected and must be refreshed",
        "legal_basis": "Small Business Act (15 USC 631 et seq.); SBA Standard Operating Procedure 50 10 (SOP 50 10) governing lender and loan requirements",
    },
    "1099-reporting": {
        "name": "1099 Reporting",
        "description": "Convert bank statements for 1099 contractor payment reporting and verification",
        "pain_point": "Businesses issuing 1099s need to verify contractor payments against bank statements. Manually cross-referencing PDFs with payment records is time-consuming and error-prone.",
        "benefit": "Convert bank statements to Excel to quickly filter and identify all contractor payments, verify 1099 amounts, and ensure IRS compliance before January filing deadlines.",
        "keywords": "bank statement for 1099, contractor payment verification, 1099 reporting bank statement tool",
        "typical_timeframe": "Full calendar year (January 1 to December 31), as 1099-NEC and 1099-MISC are reported on a calendar year basis",
        "required_by": "IRS and state tax authorities; businesses must issue 1099-NEC to each contractor paid $600 or more and file copies with the IRS",
        "key_data_points": "Payments to each contractor by name and TIN, total annual amount per payee, payment method (check, ACH, wire), payments to corporations (generally exempt from 1099)",
        "formatting_requirements": "Payments grouped by contractor/payee with annual totals, cross-referenced against W-9 information on file; separate identification of credit card payments (reported by payment processor, not payer)",
        "deadline_pressure": "1099-NEC due to contractors and IRS by January 31; late filing penalties range from $60 to $310 per form depending on how late, up to $630 for intentional disregard",
        "legal_basis": "Internal Revenue Code sections 6041-6050W (information reporting requirements); IRC section 6721-6722 (penalties for failure to file or furnish correct information returns)",
    },
    "ppp-loan-forgiveness": {
        "name": "PPP & EIDL Documentation",
        "description": "Prepare bank statements for PPP loan forgiveness and EIDL compliance documentation",
        "pain_point": "PPP and EIDL loan recipients need bank statements to document eligible expenses, payroll costs, and fund usage for SBA forgiveness applications and ongoing compliance.",
        "benefit": "Convert bank statements to structured spreadsheets to document PPP-eligible expenses, track EIDL fund usage, and prepare forgiveness application supporting documents.",
        "keywords": "PPP loan bank statement, EIDL compliance bank statement, SBA forgiveness documentation tool",
        "typical_timeframe": "8-24 week covered period for PPP forgiveness; EIDL loan term (up to 30 years) with ongoing compliance documentation of fund usage",
        "required_by": "SBA and the PPP/EIDL servicing lender for forgiveness review; SBA Office of Inspector General for post-forgiveness audits",
        "key_data_points": "Payroll costs (must be at least 60% of PPP funds), rent, utilities, mortgage interest, covered operations expenditures, PPP fund receipt and disbursement dates",
        "formatting_requirements": "Expenses categorized by PPP-eligible category (payroll, rent, utilities, etc.) with dates proving they fall within the covered period; FTE headcount evidence from payroll transactions",
        "deadline_pressure": "PPP forgiveness applications should be submitted within 10 months of the covered period end to avoid loan repayment starting; SBA OIG audits can occur years after forgiveness",
        "legal_basis": "CARES Act section 1106 (PPP forgiveness); Economic Aid Act (PPP Second Draw); SBA Interim Final Rules on PPP forgiveness (85 FR 33004)",
    },
    "immigration-visa-us": {
        "name": "US Immigration & Visa",
        "description": "Prepare bank statements for US visa applications, green card petitions, and immigration cases",
        "pain_point": "US immigration applications (H-1B, L-1, EB-5, family sponsorship) require bank statements proving financial capability. USCIS needs clear, organized financial evidence.",
        "benefit": "Convert bank statements to structured Excel spreadsheets showing balance history, income sources, and savings — formatted for USCIS evidence packages and immigration attorney review.",
        "keywords": "bank statement for US visa, green card bank statement, USCIS financial evidence, immigration bank statement converter",
        "typical_timeframe": "12 months for most family-based petitions (I-864 Affidavit of Support); EB-5 investors need to show full source-of-funds history which may span several years",
        "required_by": "USCIS adjudicating officer or consular officer at a US embassy; immigration attorney assembles the evidence package",
        "key_data_points": "Household income relative to 125% of Federal Poverty Guidelines (for I-864), asset values, evidence of consistent employment income, source of investment funds for EB-5",
        "formatting_requirements": "Statements must show account holder name matching passport, consistent with I-864 reported income; EB-5 requires detailed source-of-funds tracing with annotated transaction history",
        "deadline_pressure": "USCIS RFE (Request for Evidence) responses are due within 30-87 days depending on the notice; expired evidence packages require complete resubmission",
        "legal_basis": "Immigration and Nationality Act section 213A (Affidavit of Support); 8 CFR 213a (I-864 requirements); INA section 203(b)(5) (EB-5 investor visa regulations)",
    },
    "child-support": {
        "name": "Child Support & Alimony",
        "description": "Convert bank statements for child support calculations and family court proceedings",
        "pain_point": "Child support and alimony cases require detailed bank statement analysis to determine income, hidden assets, and lifestyle spending for family court filings.",
        "benefit": "Convert bank statements to searchable spreadsheets for income analysis, expense categorization, and financial disclosure in child support and alimony proceedings.",
        "keywords": "bank statement for child support, alimony bank statement, family court financial disclosure, income analysis tool",
        "typical_timeframe": "6-12 months of statements for initial support calculations; modification requests may require 2-3 years to show changed circumstances",
        "required_by": "Family court judge, state child support enforcement agency (Title IV-D agency), or opposing counsel during discovery",
        "key_data_points": "Gross and net income from all sources, unreported cash income, lifestyle spending inconsistent with reported income, asset transfers, hidden accounts, voluntary underemployment indicators",
        "formatting_requirements": "Income categorized by source with monthly averages, expenses separated into mandatory versus discretionary, formatted for state-specific child support worksheet calculations",
        "deadline_pressure": "Discovery responses typically due within 30 days in most state courts; temporary support orders may be entered within weeks of filing based on initial financial disclosures",
        "legal_basis": "State-specific family code and child support guidelines; federal Child Support Enforcement Act (42 USC 651-669b); Uniform Interstate Family Support Act (UIFSA)",
    },
    "workers-comp": {
        "name": "Workers' Compensation",
        "description": "Prepare bank statements for workers' compensation claims and wage verification",
        "pain_point": "Workers' compensation claims require bank statement analysis to verify pre-injury wages, track benefit payments, and identify return-to-work income for claim management.",
        "benefit": "Convert bank statements to Excel for wage verification, benefit payment tracking, and income analysis supporting workers' compensation claims and disputes.",
        "keywords": "bank statement for workers comp, wage verification statement tool, workers compensation claim documentation",
        "typical_timeframe": "12 months pre-injury for Average Weekly Wage (AWW) calculation; ongoing statements during the benefit period to detect unreported return-to-work income",
        "required_by": "Workers' compensation insurance carrier claims adjuster, employer's third-party administrator (TPA), or state workers' compensation board during disputed claims",
        "key_data_points": "Pre-injury payroll deposits to calculate AWW, overtime and bonus income, side employment income, post-injury deposits indicating undisclosed work activity",
        "formatting_requirements": "Pay deposits isolated from other income, weekly or biweekly pay periods mapped, pre-injury versus post-injury comparison, secondary employment income flagged separately",
        "deadline_pressure": "Initial claims must be filed within state-specific deadlines (30-90 days from injury in most states); wage verification documents needed before temporary disability benefits are calculated",
        "legal_basis": "State-specific workers' compensation statutes (e.g., California Labor Code Division 4; New York Workers' Compensation Law); each state has its own AWW calculation methodology",
    },
    "sec-compliance": {
        "name": "SEC Compliance",
        "description": "Convert bank statements for SEC regulatory compliance and financial reporting",
        "pain_point": "Registered investment advisors, broker-dealers, and public companies need bank statement data for SEC compliance, custody verification, and regulatory examination responses.",
        "benefit": "Convert financial institution statements to structured spreadsheets for SEC examination preparation, custody audits, and regulatory filing support.",
        "keywords": "bank statement for SEC compliance, RIA custody audit, broker-dealer bank statement, regulatory examination tool",
        "typical_timeframe": "12-36 months for SEC examination document requests; custody rule compliance requires ongoing quarterly verification",
        "required_by": "SEC Division of Examinations (formerly OCIE) staff during routine or cause examinations; independent auditors performing surprise custody examinations under Rule 206(4)-2",
        "key_data_points": "Client fund custody verification, proprietary trading account activity, segregation of client versus firm assets, fee deductions from client accounts, wire transfer activity",
        "formatting_requirements": "Account-level detail with clear identification of client versus proprietary accounts, reconciled to ADV Part 1 reported AUM; transaction types coded for examiner review",
        "deadline_pressure": "SEC document request responses typically due within 1-2 weeks during an examination; failure to produce records promptly can escalate the examination scope",
        "legal_basis": "Investment Advisers Act of 1940 sections 204 and 206; SEC Rule 206(4)-2 (custody rule); Securities Exchange Act of 1934 section 17(a) (broker-dealer recordkeeping)",
    },
    "bankruptcy-filing": {
        "name": "Bankruptcy Filing",
        "description": "Prepare bank statements for Chapter 7 and Chapter 13 bankruptcy filings",
        "pain_point": "Bankruptcy attorneys and filers need 6+ months of bank statements for means testing, Schedule I/J preparation, and trustee review in Chapter 7 and Chapter 13 cases.",
        "benefit": "Convert bank statements to Excel for quick means test analysis, income/expense categorization, and complete financial disclosure required by bankruptcy courts.",
        "keywords": "bank statement for bankruptcy, Chapter 7 bank statement, Chapter 13 means test, bankruptcy filing financial disclosure",
        "typical_timeframe": "6 months of statements for means test (CMI calculation); trustees may request up to 2 years to identify preferential or fraudulent transfers",
        "required_by": "Chapter 7 or Chapter 13 bankruptcy trustee; US Trustee's office; bankruptcy court as part of the petition schedules and Statement of Financial Affairs (SOFA)",
        "key_data_points": "Current Monthly Income (CMI) for means test, payments to creditors in the 90 days pre-filing (preferential transfers), insider payments in prior year, cash on hand at filing date",
        "formatting_requirements": "Income averaged over 6-month look-back period per means test form (B122A/B122C); expenses categorized per Schedule J; transfers over $600 in prior 2 years identified for SOFA",
        "deadline_pressure": "Bankruptcy petition triggers automatic stay immediately; however, required documents (including bank statements) must be filed within 45 days or the case may be dismissed",
        "legal_basis": "11 USC section 707(b) (means test for Chapter 7); 11 USC section 521 (debtor duties including document production); Bankruptcy Rules 1007 and 4002",
    },
    "sales-tax-reporting": {
        "name": "Sales Tax Reporting",
        "description": "Prepare bank statements for state sales tax filing and nexus analysis",
        "pain_point": "Businesses selling across multiple states need to reconcile bank deposits with sales tax collected, analyze economic nexus thresholds, and prepare multi-state filings.",
        "benefit": "Convert bank statements to Excel to reconcile sales deposits across states, verify tax collected amounts, and prepare accurate multi-state sales tax returns.",
        "keywords": "bank statement for sales tax, nexus analysis bank statement, multi-state tax reporting tool",
        "typical_timeframe": "Monthly, quarterly, or annually depending on the state and filing frequency assigned based on sales volume",
        "required_by": "State departments of revenue in each state where the business has nexus; services like TaxJar or Avalara may also need bank data for reconciliation",
        "key_data_points": "Gross sales deposits by state or channel, marketplace facilitator remittances (Amazon, Shopify), exempt sales, sales tax collected versus sales tax remitted, economic nexus threshold tracking",
        "formatting_requirements": "Sales deposits reconciled against payment processor reports (Stripe, PayPal, Square), grouped by state jurisdiction, with taxable versus exempt sales separated",
        "deadline_pressure": "Filing frequencies vary by state; California and Texas returns are due by the last day of the month following the reporting period; late filing penalties typically 5-25% of tax due",
        "legal_basis": "South Dakota v. Wayfair (2018) establishing economic nexus; state-specific sales tax statutes; Streamlined Sales Tax Agreement for participating states",
    },
}


# ---------------------------------------------------------------------------
# Helper functions — build unique content from enriched data
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helper functions — build unique content from enriched data
# ---------------------------------------------------------------------------


def _bank_features(bank):
    """Build unique feature list from bank's enriched data."""
    features = []
    if bank.get("unique_features"):
        for uf in bank["unique_features"][:4]:
            parts = uf.split(" ", 3)
            features.append({"title": " ".join(parts[:3]), "desc": " ".join(parts[3:]) if len(parts) > 3 else uf})
    if not features:
        features = [
            {"title": f"{bank['name']} format support", "desc": f"Handles all {bank['name']} statement formats including {', '.join(bank['formats'])}"},
            {"title": "Date format handling", "desc": f"Correctly parses {bank.get('date_format', 'DD/MM/YYYY')} dates used in {bank['name']} statements"},
            {"title": f"{bank['name']} column layout", "desc": f"Reads {bank.get('column_layout', 'standard column layouts')} without misalignment"},
        ]
    return features


def _prof_features(prof):
    """Build unique feature list from profession's enriched data."""
    features = [
        {"title": f"Built for {prof['name']}", "desc": prof.get("time_savings", prof["benefit"])},
    ]
    if prof.get("software_preferences"):
        features.append({"title": "Software compatible", "desc": f"Output works directly with {', '.join(prof['software_preferences'])}"})
    if prof.get("peak_periods"):
        features.append({"title": "Peak period ready", "desc": f"Handles high volumes during {prof['peak_periods'].split('.')[0].lower()}"})
    if prof.get("unique_challenges"):
        features.append({"title": "Solves your challenges", "desc": prof["unique_challenges"][0]})
    return features


def _fmt_features(fmt):
    """Build unique feature list from format's enriched data."""
    features = [
        {"title": f"{fmt['name']} output", "desc": fmt.get("description", f"Clean {fmt['name']} file with all transaction data")},
    ]
    if fmt.get("unique_advantages"):
        features.append({"title": "Unique advantage", "desc": fmt["unique_advantages"]})
    if fmt.get("best_for"):
        features.append({"title": "Best for", "desc": fmt["best_for"]})
    return features


def _sw_features(sw):
    """Build unique feature list from software's enriched data."""
    features = [
        {"title": f"{sw['name']} ready", "desc": f"Output formatted as {sw['import_format']} matching {sw['name']}'s expected column structure"},
    ]
    if sw.get("unique_integration"):
        features.append({"title": "Smart integration", "desc": sw["unique_integration"]})
    if sw.get("market_position"):
        features.append({"title": "Industry standard", "desc": sw["market_position"]})
    return features


def _uc_features(uc):
    """Build unique feature list from use case's enriched data."""
    features = [
        {"title": f"Built for {uc['name']}", "desc": uc["benefit"]},
    ]
    if uc.get("key_data_points"):
        features.append({"title": "Key data extracted", "desc": uc["key_data_points"]})
    if uc.get("deadline_pressure"):
        features.append({"title": "Deadline ready", "desc": uc["deadline_pressure"]})
    return features


# ---------------------------------------------------------------------------
# Page generators — each returns a dict of {slug: page_data}
# ---------------------------------------------------------------------------


def _generate_bank_pages():
    """Generate /tools/convert-{bank}-statement-to-excel pages."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        slug = f"convert-{bank_slug}-statement-to-excel"
        features = _bank_features(bank)

        deep_dive = None
        if bank.get("common_issues"):
            deep_dive = {
                "heading": f"Why {bank['name']} Statements Need Specialist Conversion",
                "content": (
                    f"<p>{bank['name']} statements use {bank.get('date_format', 'a specific date format')} dates "
                    f"with {bank.get('column_layout', 'a unique column layout')}. "
                    f"Common parsing challenges include: {bank['common_issues']}.</p>"
                    f"<p>BankScan AI's engine is specifically trained on {bank['full_name']} layouts, "
                    f"so it handles these edge cases automatically — no manual cleanup required.</p>"
                ),
            }

        bank_info_box = None
        if bank.get("digital_banking_tip"):
            bank_info_box = {"title": f"How to Download Your {bank['name']} Statement", "content": bank["digital_banking_tip"]}

        pro_tip = None
        if bank.get("history_note"):
            pro_tip = {"title": f"{bank['name']} — Did You Know?", "content": bank["history_note"]}

        pages[slug] = {
            "type": "bank",
            "title": f"Convert {bank['name']} Bank Statement to Excel | Free AI Converter",
            "h1": f"Convert {bank['name']} Bank Statements to Excel Instantly",
            "meta_description": f"Upload your {bank['name']} bank statement PDF and get a clean Excel spreadsheet in seconds. AI-powered converter handles {bank.get('date_format', 'all date formats')} dates and {bank.get('column_layout', 'all layouts')}.",
            "keywords": f"convert {bank['name']} statement to Excel, {bank['name']} PDF to Excel, {bank['name']} statement converter, {bank['name']} bank statement parser",
            "bank": bank,
            "bank_slug": bank_slug,
            "intro_heading": f"Why Convert {bank['name']} Statements with BankScan AI?",
            "intro": f"Need to convert a {bank['full_name']} bank statement to Excel? BankScan AI uses artificial intelligence to parse your {bank['name']} PDF statement — handling {bank.get('date_format', 'all date formats')} dates and {bank.get('column_layout', 'unique column layouts')} — and produce a clean, formatted spreadsheet ready for Xero, QuickBooks, Sage, or any bookkeeping software.",
            "detail_heading": f"Understanding {bank['name']} Statement Formats",
            "detail_paragraph": f"{bank['statement_notes']} BankScan AI has been specifically trained on {bank['full_name']} statement layouts across all supported formats ({', '.join(bank['formats'])}), so it correctly handles the column alignment, date parsing, and transaction description extraction that generic PDF tools get wrong.",
            "deep_dive": deep_dive,
            "bank_info_box": bank_info_box,
            "pro_tip": pro_tip,
            "features": features,
            "features_heading": f"What Makes BankScan AI Best for {bank['name']}",
            "trust_stats": [
                {"value": "99%+", "label": f"{bank['name']} Accuracy"},
                {"value": "30s", "label": "Per Statement"},
                {"value": ", ".join(bank["formats"]), "label": "Formats Supported"},
                {"value": "Free", "label": "Tier Available"},
            ],
            "steps": [
                {"title": f"Upload your {bank['name']} statement", "desc": f"Drag and drop your {bank['name']} bank statement PDF. We support all {bank['name']} statement formats including {', '.join(bank['formats'])}."},
                {"title": "AI parses every transaction", "desc": f"Our AI engine reads your {bank['name']} statement, handles {bank.get('date_format', 'the date format')} dates, and extracts descriptions, amounts, and running balances."},
                {"title": "Download your Excel file", "desc": "Get a formatted .xlsx spreadsheet with all transactions neatly organised. Ready to import into your accounting software."},
            ],
            "faqs": [
                {"q": f"What {bank['name']} statement formats does BankScan AI support?", "a": f"BankScan AI supports {bank['name']} statements in {', '.join(bank['formats'])} format. {bank.get('digital_banking_tip', 'Upload statements downloaded from online banking or scanned from paper.')}"},
                {"q": f"How does BankScan AI handle {bank['name']}'s specific layout?", "a": f"{bank['name']} uses {bank.get('column_layout', 'a specific column layout')} with {bank.get('date_format', 'specific date')} formatting. BankScan AI is trained on this exact layout so it parses every transaction correctly — even when {bank.get('common_issues', 'complex formatting is present')}."},
                {"q": f"Can I batch-convert multiple {bank['name']} statements?", "a": f"Yes. With a paid plan, upload multiple {bank['name']} statement PDFs and convert them all at once. Each produces its own Excel file."},
                {"q": "Is my data secure?", "a": "Absolutely. Your bank statements are processed in memory and deleted immediately after conversion. We never store your financial data."},
            ],
            "cta_heading": f"Convert Your {bank['name']} Statements Today",
            "cta_subtext": f"Join thousands of professionals who use BankScan AI to convert {bank['name']} statements. Free tier available — no credit card required.",
            "cta_text": f"Convert Your {bank['name']} Statement Now",
        }
    return pages


def _generate_profession_pages():
    """Generate /tools/bank-statement-converter-for-{profession} pages."""
    pages = {}
    for prof_slug, prof in PROFESSIONS.items():
        slug = f"bank-statement-converter-for-{prof_slug}"
        features = _prof_features(prof)

        workflow_section = None
        if prof.get("workflow_detail"):
            workflow_section = {
                "heading": f"How {prof['name']} Use Bank Statement Data",
                "content": prof["workflow_detail"],
                "jargon_terms": prof.get("industry_jargon", []),
            }

        compliance_section = None
        if prof.get("compliance_requirements"):
            compliance_section = {
                "heading": f"Compliance Considerations for {prof['name']}",
                "content": f"When handling bank statement data, {prof['name'].lower()} must comply with relevant regulations.",
                "key_points": prof["compliance_requirements"] if isinstance(prof["compliance_requirements"], list) else [prof["compliance_requirements"]],
            }

        pro_tip = None
        if prof.get("peak_periods"):
            pro_tip = {"title": f"Peak Period Tip for {prof['name']}", "content": prof["peak_periods"]}

        pages[slug] = {
            "type": "profession",
            "title": f"Best Bank Statement Converter for {prof['name']} | BankScan AI",
            "h1": f"Bank Statement Converter for {prof['name']}",
            "meta_description": f"AI-powered bank statement converter built for {prof['name'].lower()}. {prof.get('time_savings', 'Upload any bank statement PDF, get a clean Excel spreadsheet in seconds.')} Try free.",
            "keywords": ", ".join(prof["keywords"]),
            "profession": prof,
            "profession_slug": prof_slug,
            "intro_heading": f"Why {prof['name']} Choose BankScan AI",
            "intro": prof["pain_point"],
            "solution_heading": f"How BankScan AI Helps {prof['name']}",
            "solution": prof["benefit"],
            "detail_heading": f"The {prof['name']} Bank Statement Workflow",
            "detail_paragraph": (
                f"For {prof['name'].lower()}, the core challenge is turning unstructured PDF data into actionable, "
                f"organised records quickly enough to stay on top of deadlines. "
                f"BankScan AI eliminates this bottleneck by handling the data extraction automatically, "
                f"so {prof['name'].lower()} can focus on analysis, client advice, and compliance rather than manual data entry."
            ),
            "workflow_section": workflow_section,
            "compliance_section": compliance_section,
            "pro_tip": pro_tip,
            "features": features,
            "features_heading": f"Features {prof['name']} Love",
            "steps": [
                {"title": "Upload any bank statement PDF", "desc": f"Supports all major UK and US banks. {prof.get('time_savings', 'Convert in seconds, not hours.')}"},
                {"title": "AI extracts every transaction", "desc": f"Our AI reads the PDF and pulls out dates, descriptions, amounts, and balances — formatted for {prof['name'].lower()} workflows."},
                {"title": "Download your spreadsheet", "desc": f"Get a formatted Excel file ready for {', '.join(prof.get('software_preferences', ['your accounting software'])[:3]) if prof.get('software_preferences') else 'your accounting software'} or direct analysis."},
            ],
            "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
            "faqs": [
                {"q": f"Is BankScan AI suitable for {prof['name'].lower()}?", "a": f"Yes. BankScan AI is specifically designed for {prof['name'].lower()}. {prof['benefit']} {prof.get('time_savings', '')}"},
                {"q": f"What software does BankScan AI integrate with for {prof['name'].lower()}?", "a": f"BankScan AI outputs Excel and CSV files compatible with {', '.join(prof.get('software_preferences', ['Xero', 'QuickBooks', 'Sage']))}. The format matches each platform's import requirements."},
                {"q": "Which banks are supported?", "a": "BankScan AI supports all major UK and US banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Chase, Bank of America, Wells Fargo, and 40+ more."},
                {"q": f"How does BankScan AI handle compliance for {prof['name'].lower()}?", "a": f"BankScan AI processes statements in memory and deletes data immediately after conversion. {prof.get('compliance_requirements', 'Your data is never stored.')}"},
            ],
            "cta_heading": f"Start Converting — Built for {prof['name']}",
            "cta_subtext": f"Join {prof['name'].lower()} across the UK who save hours every week with BankScan AI.",
            "cta_text": f"Try Free — Built for {prof['name']}",
        }
    return pages


def _generate_receipt_pages():
    """Generate /tools/receipt-scanner-for-{profession} pages."""
    pages = {}
    for prof_slug, prof in PROFESSIONS.items():
        slug = f"receipt-scanner-for-{prof_slug}"
        pages[slug] = {
            "type": "receipt",
            "title": f"AI Receipt Scanner for {prof['name']} | Photo to Excel",
            "h1": f"Receipt Scanner for {prof['name']}",
            "meta_description": f"Scan receipts and convert them to Excel spreadsheets instantly. AI-powered receipt scanner built for {prof['name'].lower()}. Supports photos, scans, and PDFs.",
            "keywords": f"receipt scanner for {prof['name'].lower()}, receipt to Excel, receipt OCR, expense tracker {prof['name'].lower()}",
            "profession": prof,
            "profession_slug": prof_slug,
            "intro_heading": f"Receipt Scanning Made Simple for {prof['name']}",
            "intro": f"Stop manually typing receipt data. BankScan AI uses artificial intelligence to read your receipts — whether they're photos from your phone, scanned images, or PDF attachments — and converts them to structured Excel spreadsheets perfect for {prof['name'].lower()} workflows.",
            "features": [
                {"title": "Photo to spreadsheet", "desc": "Snap a photo of any receipt and get structured data in seconds — merchant, date, items, VAT, and total"},
                {"title": f"Built for {prof['name'].lower()}", "desc": f"{prof.get('time_savings', prof['benefit'])}"},
                {"title": "VAT extraction", "desc": "Automatically identifies and extracts VAT amounts for easy VAT return preparation and expense claims"},
                {"title": "Batch processing", "desc": "Upload multiple receipts at once and get a single consolidated spreadsheet with all items categorised"},
            ],
            "features_heading": f"Receipt Scanner Features for {prof['name']}",
            "steps": [
                {"title": "Upload receipt photos or PDFs", "desc": f"Take a photo of any receipt, or upload scanned images and PDFs. Perfect for {prof['name'].lower()} tracking client expenses."},
                {"title": "AI reads every line item", "desc": "Our AI extracts the merchant name, date, items, amounts, VAT, and total with high accuracy."},
                {"title": "Download your spreadsheet", "desc": f"Get a formatted Excel file organised for {prof['name'].lower()} expense tracking, VAT reclaims, and bookkeeping."},
            ],
            "pro_tip": {"title": f"Receipt Tip for {prof['name']}", "content": f"Combine BankScan AI's receipt scanner with the bank statement converter to match receipts against bank transactions — creating a complete audit trail for {prof['name'].lower()} records."},
            "faqs": [
                {"q": f"Can {prof['name'].lower()} use this for expense tracking?", "a": f"Absolutely. BankScan AI's receipt scanner is perfect for {prof['name'].lower()} who need to track expenses. {prof['benefit']}"},
                {"q": "Does it handle VAT receipts?", "a": "Yes. BankScan AI extracts VAT amounts from receipts, making it easy to prepare VAT returns and expense claims."},
                {"q": "Can I scan multiple receipts at once?", "a": "Yes. With a paid plan, upload multiple receipt photos in one batch and get a single consolidated spreadsheet."},
                {"q": f"How does this help {prof['name'].lower()} specifically?", "a": f"{prof.get('workflow_detail', prof['pain_point'])} BankScan AI's receipt scanner eliminates the manual data entry bottleneck."},
            ],
            "cta_heading": f"Scan Receipts — Built for {prof['name']}",
            "cta_subtext": f"Combine with bank statement conversion for a complete financial data extraction workflow.",
            "cta_text": "Scan Your Receipts Free",
        }
    return pages


def _generate_bank_format_pages():
    """Generate /tools/{bank}-pdf-to-{format} pages for ALL banks."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        for fmt_slug, fmt in FORMATS.items():
            slug = f"{bank_slug}-pdf-to-{fmt_slug}"

            bank_info_box = None
            if bank.get("digital_banking_tip"):
                bank_info_box = {"title": f"Getting Your {bank['name']} PDF", "content": bank["digital_banking_tip"]}

            pages[slug] = {
                "type": "bank_format",
                "title": f"Convert {bank['name']} PDF to {fmt['name']} | AI-Powered Converter",
                "h1": f"{bank['name']} PDF Statement to {fmt['name']}",
                "meta_description": f"Convert {bank['name']} bank statement PDFs to {fmt['name']} format instantly. Handles {bank.get('date_format', 'all date formats')} dates and {bank.get('column_layout', 'all layouts')}. {fmt.get('best_for', 'Fast, accurate, secure.')}",
                "keywords": f"{bank['name']} PDF to {fmt['name']}, {bank['name']} statement to {fmt['name']}, convert {bank['name']} PDF {fmt_slug}",
                "bank": bank,
                "bank_slug": bank_slug,
                "format": fmt,
                "format_slug": fmt_slug,
                "intro_heading": f"Why Convert {bank['name']} to {fmt['name']}?",
                "intro": f"Convert your {bank['full_name']} bank statement from PDF to {fmt['name']} format. {fmt.get('description', 'Get a clean output file')} — BankScan AI handles {bank.get('date_format', 'all date formats')} dates and {bank.get('column_layout', 'the column layout')} automatically.",
                "detail_heading": f"{fmt['name']} Output Details",
                "detail_paragraph": f"{fmt.get('description', fmt['name'] + ' is a widely used format.')} {fmt.get('technical_notes', '')} {bank['statement_notes']}",
                "bank_info_box": bank_info_box,
                "features": [
                    {"title": f"{bank['name']} optimised", "desc": f"Trained on {bank['name']}'s {bank.get('column_layout', 'specific layout')} with {bank.get('date_format', 'correct date')} parsing"},
                    {"title": f"{fmt['name']} output", "desc": fmt.get("unique_advantages", f"Clean {fmt['name']} file with all transaction data preserved")},
                    {"title": "Format compatibility", "desc": f"Compatible with {', '.join(fmt.get('import_compatible', ['major accounting software'])[:3]) if fmt.get('import_compatible') else 'major accounting software'}"},
                ],
                "features_heading": f"{bank['name']} to {fmt['name']} Features",
                "steps": [
                    {"title": f"Upload your {bank['name']} PDF", "desc": f"Drop your {bank['name']} bank statement PDF into BankScan AI. Supports {', '.join(bank['formats'])} formats."},
                    {"title": "AI extracts all transactions", "desc": f"Our engine parses {bank['name']}'s {bank.get('date_format', 'date format')} dates and {bank.get('column_layout', 'column layout')} with 99%+ accuracy."},
                    {"title": f"Download as {fmt['name']}", "desc": f"Get your data as {fmt['name']} ({fmt['extension'] or 'cloud format'}). {fmt.get('best_for', 'Ready for your workflow.')}"},
                ],
                "faqs": [
                    {"q": f"Can I convert {bank['name']} statements to {fmt['name']} for free?", "a": f"Yes. BankScan AI offers a free tier for converting {bank['name']} statements to {fmt['name']}. Paid plans offer batch conversion and higher volumes."},
                    {"q": f"Will the {fmt['name']} file preserve all transaction details?", "a": f"Yes. The {fmt['name']} output includes dates ({bank.get('date_format', 'correctly parsed')}), descriptions, amounts, and running balances from your {bank['name']} statement. {fmt.get('technical_notes', '')}"},
                    {"q": f"What are the limitations of {fmt['name']} format?", "a": fmt.get("limitations", f"{fmt['name']} works with most software and workflows.")},
                ],
                "cta_heading": f"Convert {bank['name']} to {fmt['name']} Now",
                "cta_subtext": f"{fmt.get('best_for', 'Fast, accurate conversion with a free tier available.')}",
                "cta_text": f"Convert {bank['name']} to {fmt['name']} Free",
            }
    return pages


def _generate_software_pages():
    """Generate /tools/import-bank-statement-to-{software} pages."""
    pages = {}
    for sw_slug, sw in ACCOUNTING_SOFTWARE.items():
        slug = f"import-bank-statement-to-{sw_slug}"
        features = _sw_features(sw)

        import_guide = None
        if sw.get("import_steps"):
            steps = [s.strip() for s in sw["import_steps"].split(".") if s.strip()]
            import_guide = {
                "heading": f"Step-by-Step: Import into {sw['name']}",
                "intro": f"Here's exactly how to import your converted bank statement into {sw['name']}:",
                "steps": steps,
                "common_errors": sw.get("common_import_errors", ""),
            }

        pages[slug] = {
            "type": "software",
            "title": f"Import Bank Statement to {sw['name']} | PDF to {sw['name']} Converter",
            "h1": f"Import Bank Statements into {sw['name']}",
            "meta_description": f"Convert bank statement PDFs to {sw['import_format']} for direct import into {sw['name']}. {sw.get('market_position', 'AI-powered converter for accountants.')}",
            "keywords": f"import bank statement to {sw['name']}, {sw['name']} bank statement import, {sw['name']} statement converter, PDF to {sw['name']}",
            "software": sw,
            "software_slug": sw_slug,
            "intro_heading": f"Why Import Bank Statements into {sw['name']}?",
            "intro": f"{sw['description']} But importing bank statements from PDF into {sw['name']} requires converting them to {sw['import_format']} first — and that's where BankScan AI comes in.",
            "solution_heading": f"BankScan AI + {sw['name']} Integration",
            "solution": sw["import_notes"],
            "detail_heading": f"How {sw['name']} Bank Import Works",
            "detail_paragraph": f"Importing bank transactions into {sw['name']} typically involves downloading a {sw['import_format']} file from your bank's online portal — but many clients provide paper statements or PDF downloads that {sw['name']} cannot read directly. {sw['import_notes']}",
            "import_guide": import_guide,
            "features": features,
            "features_heading": f"Why BankScan AI for {sw['name']}",
            "pro_tip": {"title": f"{sw['name']} Column Format", "content": sw.get("column_mapping", f"BankScan AI formats output to match {sw['name']}'s expected column structure.")} if sw.get("column_mapping") else None,
            "steps": [
                {"title": "Upload your bank statement PDF", "desc": "Drag and drop any bank statement PDF into BankScan AI. Supports HSBC, Barclays, Lloyds, NatWest, Monzo, and 40+ more banks."},
                {"title": f"AI converts to {sw['import_format']}", "desc": f"Our AI extracts every transaction and formats as {sw['import_format']} with {sw.get('column_mapping', 'the correct columns')} — ready for {sw['name']}."},
                {"title": f"Import into {sw['name']}", "desc": sw.get('import_steps', 'Upload the file and start reconciling.')},
            ],
            "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
            "faqs": [
                {"q": f"Does BankScan AI work with {sw['name']}?", "a": f"Yes. BankScan AI produces {sw['import_format']} files with {sw.get('column_mapping', 'columns')} — exactly what {sw['name']} expects for bank statement import."},
                {"q": f"What date format does {sw['name']} require?", "a": f"{sw['name']} requires dates in {sw.get('date_format_required', 'DD/MM/YYYY')} format. BankScan AI automatically formats dates correctly for {sw['name']} import."},
                {"q": f"What are common {sw['name']} import errors?", "a": sw.get("common_import_errors", f"Most issues are related to date format or column mapping. BankScan AI handles these automatically.")},
                {"q": "Is the free tier enough for occasional use?", "a": "Yes. The free tier lets you convert a limited number of statements per month — ideal for trying BankScan AI before committing to a paid plan."},
            ],
            "cta_heading": f"Import Bank Statements into {sw['name']} Today",
            "cta_subtext": sw.get('market_position', f"Join thousands using BankScan AI with {sw['name']}."),
            "cta_text": f"Convert for {sw['name']} — Free",
        }
    return pages


def _generate_use_case_pages():
    """Generate /tools/bank-statement-for-{use-case} pages."""
    pages = {}
    for uc_slug, uc in USE_CASES.items():
        slug = f"bank-statement-for-{uc_slug}"
        features = _uc_features(uc)

        data_requirements = None
        if uc.get("key_data_points"):
            items = [{"label": dp.strip().split(" ")[0], "detail": dp.strip()} for dp in uc["key_data_points"].split(",")]
            data_requirements = {
                "heading": f"What Data You Need for {uc['name']}",
                "intro": f"When preparing bank statements for {uc['name'].lower()}, these are the key data points {uc.get('required_by', 'reviewers')} look for:",
                "items": items[:5],
            }

        compliance_section = None
        if uc.get("legal_basis"):
            compliance_section = {
                "heading": f"Legal & Regulatory Context",
                "content": f"Bank statements for {uc['name'].lower()} are typically required under: {uc['legal_basis']}",
                "key_points": [f"Typical timeframe: {uc.get('typical_timeframe', '3-6 months')}", f"Required by: {uc.get('required_by', 'Various')}"],
            }

        challenges_section = None
        if uc.get("formatting_requirements"):
            challenges_section = {
                "heading": f"Formatting Your Statements for {uc['name']}",
                "intro": f"Getting bank statements right for {uc['name'].lower()} requires attention to specific formatting requirements:",
                "items": [uc["formatting_requirements"], f"Deadline pressure: {uc.get('deadline_pressure', 'Varies by case')}"],
            }

        pages[slug] = {
            "type": "use_case",
            "title": f"Bank Statement Converter for {uc['name']} | BankScan AI",
            "h1": f"Convert Bank Statements for {uc['name']}",
            "meta_description": f"{uc['description']}. AI-powered converter supports all UK and US banks. {uc.get('deadline_pressure', 'Upload PDF, get Excel in seconds.')}",
            "keywords": uc["keywords"],
            "use_case": uc,
            "use_case_slug": uc_slug,
            "intro_heading": f"Bank Statements for {uc['name']}: The Challenge",
            "intro": uc["pain_point"],
            "solution_heading": f"How BankScan AI Helps with {uc['name']}",
            "solution": uc["benefit"],
            "data_requirements": data_requirements,
            "compliance_section": compliance_section,
            "challenges_section": challenges_section,
            "features": features,
            "features_heading": f"Why BankScan AI for {uc['name']}",
            "steps": [
                {"title": "Upload your bank statement PDF", "desc": f"Supports all major banks. {uc.get('typical_timeframe', 'Upload statements for any period')} of statements? No problem."},
                {"title": "AI extracts every transaction", "desc": f"Our AI pulls out dates, descriptions, amounts, and balances — the {uc.get('key_data_points', 'key data points')[:60] if uc.get('key_data_points') else 'data'} you need."},
                {"title": f"Use for {uc['name'].lower()}", "desc": f"Download a clean Excel or CSV file formatted for {uc['name'].lower()}. {uc.get('formatting_requirements', 'Ready to submit, analyse, or share.')}"},
            ],
            "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
            "faqs": [
                {"q": f"Can I use BankScan AI for {uc['name'].lower()}?", "a": f"Yes. BankScan AI converts bank statement PDFs to structured spreadsheets ideal for {uc['name'].lower()}. {uc['benefit']}"},
                {"q": f"How many months of statements do I need for {uc['name'].lower()}?", "a": f"Typically {uc.get('typical_timeframe', '3-6 months')} of bank statements are required. {uc.get('required_by', 'The reviewing party')} will specify the exact period needed."},
                {"q": "Is my financial data secure?", "a": "Absolutely. Bank statements are processed in memory and deleted immediately after conversion. We never store your financial data."},
                {"q": f"What specific data is needed for {uc['name'].lower()}?", "a": f"Key data points include: {uc.get('key_data_points', 'income, outgoings, balances, and regular payments')}. BankScan AI extracts all of this automatically."},
            ],
            "cta_heading": f"Prepare Statements for {uc['name']} — Fast",
            "cta_subtext": f"{uc.get('deadline_pressure', 'Convert bank statements in seconds, not hours.')}",
            "cta_text": f"Convert for {uc['name']} — Free",
        }
    return pages


def _generate_bank_profession_combo_pages():
    """Generate /tools/{bank}-statement-for-{profession} pages for popular combos."""
    pages = {}
    combo_banks = BANKS
    combo_professions = {k: v for k, v in PROFESSIONS.items() if v.get("combo_eligible")}

    for bank_slug, bank in combo_banks.items():
        for prof_slug, prof in combo_professions.items():
            slug = f"{bank_slug}-statement-for-{prof_slug}"

            bank_info_box = None
            if bank.get("common_issues"):
                bank_info_box = {"title": f"{bank['name']} Statement Quirks", "content": f"{bank['name']} uses {bank.get('date_format', 'specific date formats')} with {bank.get('column_layout', 'a specific layout')}. Common issues: {bank['common_issues']}. BankScan AI handles all of these automatically."}

            workflow_section = None
            if prof.get("workflow_detail"):
                workflow_section = {
                    "heading": f"How {prof['name']} Handle {bank['name']} Statements",
                    "content": f"When working with {bank['name']} statements, {prof['name'].lower()} need to {prof['workflow_detail'][:200]}",
                    "jargon_terms": prof.get("industry_jargon", [])[:5],
                }

            pages[slug] = {
                "type": "bank_profession",
                "title": f"Convert {bank['name']} Statement to Excel for {prof['name']} | BankScan AI",
                "h1": f"{bank['name']} Statement Converter for {prof['name']}",
                "meta_description": f"AI-powered {bank['name']} bank statement converter for {prof['name'].lower()}. Handles {bank.get('date_format', 'all formats')} dates and {bank.get('column_layout', 'complex layouts')}. Try free.",
                "keywords": f"{bank['name']} statement converter for {prof['name'].lower()}, {bank['name']} PDF to Excel {prof['name'].lower()}, {prof['name'].lower()} {bank['name']} tool",
                "bank": bank,
                "bank_slug": bank_slug,
                "profession": prof,
                "profession_slug": prof_slug,
                "intro_heading": f"{bank['name']} Statements + {prof['name']} Workflow",
                "intro": f"As {prof['singular'].lower() if prof['singular'][0].lower() not in 'aeiou' else 'an ' + prof['singular'].lower()}, you regularly handle {bank['name']} bank statements. {prof['pain_point']}",
                "solution_heading": f"BankScan AI: {bank['name']} to Excel for {prof['name']}",
                "solution": f"BankScan AI converts {bank['name']} statements to Excel automatically — handling {bank.get('date_format', 'date formats')} and {bank.get('column_layout', 'column layouts')}. {prof['benefit']}",
                "detail_heading": f"About {bank['name']} Statement Format",
                "detail_paragraph": bank["statement_notes"],
                "bank_info_box": bank_info_box,
                "workflow_section": workflow_section,
                "features": _bank_features(bank)[:2] + _prof_features(prof)[:2],
                "features_heading": f"{bank['name']} + {prof['name']} Features",
                "steps": [
                    {"title": f"Upload {bank['name']} statement", "desc": f"Drag and drop your {bank['name']} PDF. Supports {', '.join(bank['formats'])}. Our AI handles {bank.get('date_format', 'the date format')} automatically."},
                    {"title": "AI parses the statement", "desc": f"Our AI understands {bank['name']}'s {bank.get('column_layout', 'specific layout')} and extracts every transaction with 99%+ accuracy."},
                    {"title": f"Use in your {prof['name'].lower()} workflow", "desc": f"Download Excel ready for {', '.join(prof.get('software_preferences', ['your software'])[:2]) if prof.get('software_preferences') else 'your software'} or direct analysis."},
                ],
                "faqs": [
                    {"q": f"Is BankScan AI good for {prof['name'].lower()} handling {bank['name']} statements?", "a": f"Yes. BankScan AI is trained on {bank['name']}'s {bank.get('column_layout', 'format')} and designed for {prof['name'].lower()}. {prof['benefit']}"},
                    {"q": f"What {bank['name']} formats are supported?", "a": f"BankScan AI supports {bank['name']} statements in {', '.join(bank['formats'])} format. {bank.get('digital_banking_tip', 'Download from online banking or scan paper statements.')}"},
                    {"q": f"What software do {prof['name'].lower()} use with this?", "a": f"Common choices for {prof['name'].lower()} include {', '.join(prof.get('software_preferences', ['Xero', 'QuickBooks', 'Sage'])[:4])}. BankScan AI output is compatible with all of them."},
                    {"q": "Is there a free option?", "a": "Yes. BankScan AI offers a free tier so you can try the converter with no commitment. Paid plans from $9.99/month unlock batch conversion."},
                ],
                "cta_heading": f"Convert {bank['name']} Statements — Built for {prof['name']}",
                "cta_subtext": f"Handles {bank.get('date_format', 'all date formats')} dates, {bank.get('column_layout', 'complex layouts')}, and {', '.join(bank['formats'])} formats automatically.",
                "cta_text": f"Convert {bank['name']} Statements Free",
            }
    return pages


def _generate_bank_software_pages():
    """Generate /tools/import-{bank}-to-{software} pages."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        for sw_slug, sw in ACCOUNTING_SOFTWARE.items():
            slug = f"import-{bank_slug}-to-{sw_slug}"

            import_guide = None
            if sw.get("import_steps"):
                steps = [s.strip() for s in sw["import_steps"].split(".") if s.strip()]
                import_guide = {
                    "heading": f"Import {bank['name']} Data into {sw['name']}",
                    "intro": f"After converting your {bank['name']} statement with BankScan AI:",
                    "steps": steps,
                    "common_errors": sw.get("common_import_errors", ""),
                }

            pages[slug] = {
                "type": "bank_software",
                "title": f"Import {bank['name']} Statement to {sw['name']} | AI Converter",
                "h1": f"Import {bank['name']} Bank Statement into {sw['name']}",
                "meta_description": f"Convert {bank['name']} PDFs ({bank.get('date_format', 'all formats')}) to {sw['import_format']} for {sw['name']} import. Handles {bank.get('column_layout', 'complex layouts')} automatically.",
                "keywords": f"import {bank['name']} to {sw['name']}, {bank['name']} statement {sw['name']}, {bank['name']} PDF to {sw['name']}, {bank['name']} {sw['name']} import",
                "bank": bank,
                "bank_slug": bank_slug,
                "software": sw,
                "software_slug": sw_slug,
                "intro_heading": f"{bank['name']} + {sw['name']}: Bridge the Gap",
                "intro": f"Need to get your {bank['full_name']} bank statement into {sw['name']}? {bank['name']} uses {bank.get('date_format', 'specific date formats')} and {bank.get('column_layout', 'a specific layout')} — which needs converting to {sw['import_format']} ({sw.get('column_mapping', 'the right columns')}) for {sw['name']} import.",
                "solution_heading": f"How BankScan AI Converts {bank['name']} for {sw['name']}",
                "solution": f"{sw['import_notes']} {bank['statement_notes']}",
                "import_guide": import_guide,
                "features": [
                    {"title": f"{bank['name']} optimised", "desc": f"Trained on {bank['name']}'s {bank.get('column_layout', 'layout')} with {bank.get('date_format', 'correct date')} parsing"},
                    {"title": f"{sw['name']} formatted", "desc": f"Output as {sw['import_format']} with {sw.get('column_mapping', 'columns matching import requirements')}"},
                    {"title": "Date conversion", "desc": f"Converts {bank.get('date_format', 'bank dates')} to {sw.get('date_format_required', 'the required format')} automatically"},
                ],
                "features_heading": f"{bank['name']} to {sw['name']} Features",
                "pro_tip": {"title": f"{sw['name']} Import Tip", "content": sw.get("column_mapping", f"BankScan AI formats output to match {sw['name']}'s expected column structure.")} if sw.get("column_mapping") else None,
                "steps": [
                    {"title": f"Upload your {bank['name']} PDF", "desc": f"Drag and drop your {bank['name']} statement. Supports {', '.join(bank['formats'])} with {bank.get('date_format', 'correct date')} handling."},
                    {"title": f"AI converts for {sw['name']}", "desc": f"Our AI parses {bank['name']}'s layout, converts dates to {sw.get('date_format_required', 'the required format')}, and outputs {sw['import_format']}."},
                    {"title": f"Import into {sw['name']}", "desc": sw.get("import_steps", f"Upload the converted file into {sw['name']} and start reconciling transactions immediately.")},
                ],
                "faqs": [
                    {"q": f"Can I import {bank['name']} statements directly into {sw['name']}?", "a": f"{bank['name']} PDF statements can't be imported directly into {sw['name']}. BankScan AI converts them to {sw['import_format']} with {sw.get('column_mapping', 'the correct format')} that {sw['name']} accepts."},
                    {"q": f"Does the date format work with {sw['name']}?", "a": f"Yes. BankScan AI converts {bank['name']}'s {bank.get('date_format', 'date format')} to {sw.get('date_format_required', 'the required format')} automatically."},
                    {"q": f"What are common {bank['name']} to {sw['name']} issues?", "a": f"{bank.get('common_issues', 'Bank statements have specific formatting challenges.')} BankScan AI handles these automatically. {sw.get('common_import_errors', '')}"},
                    {"q": "Is there a free option?", "a": "Yes. Free tier available. Paid plans from $9.99/month for higher volumes."},
                ],
                "cta_heading": f"Import {bank['name']} into {sw['name']} Today",
                "cta_subtext": f"Automatic date conversion ({bank.get('date_format', 'bank format')} to {sw.get('date_format_required', 'software format')}), column mapping, and format validation.",
                "cta_text": f"Import {bank['name']} to {sw['name']} Free",
            }
    return pages


def _generate_bank_usecase_pages():
    """Generate /tools/{bank}-statement-for-{use-case} pages."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        for uc_slug, uc in USE_CASES.items():
            slug = f"{bank_slug}-statement-for-{uc_slug}"

            bank_info_box = None
            if bank.get("digital_banking_tip"):
                bank_info_box = {"title": f"Getting Your {bank['name']} Statement for {uc['name']}", "content": f"{bank['digital_banking_tip']} Download {uc.get('typical_timeframe', 'the required period')} of statements."}

            pages[slug] = {
                "type": "bank_usecase",
                "title": f"Convert {bank['name']} Statement for {uc['name']} | BankScan AI",
                "h1": f"{bank['name']} Statement Converter for {uc['name']}",
                "meta_description": f"Convert your {bank['name']} bank statement to Excel for {uc['name'].lower()}. Handles {bank.get('date_format', 'all formats')} dates. {uc.get('deadline_pressure', 'Fast, accurate, instant.')}",
                "keywords": f"{bank['name']} statement for {uc['name'].lower()}, convert {bank['name']} PDF {uc['name'].lower()}, {bank['name']} bank statement {uc_slug}",
                "bank": bank,
                "bank_slug": bank_slug,
                "use_case": uc,
                "use_case_slug": uc_slug,
                "intro_heading": f"{bank['name']} Statements for {uc['name']}",
                "intro": f"Preparing {bank['name']} bank statements for {uc['name'].lower()}? {uc['pain_point']}",
                "solution_heading": f"Convert {bank['name']} Statements Instantly",
                "solution": f"BankScan AI converts your {bank['name']} statement PDF to a clean Excel spreadsheet in seconds — handling {bank.get('date_format', 'the date format')} and {bank.get('column_layout', 'column layout')} automatically. {uc['benefit']}",
                "detail_heading": f"About {bank['name']} Statement Format",
                "detail_paragraph": bank["statement_notes"],
                "bank_info_box": bank_info_box,
                "features": [
                    {"title": f"{bank['name']} optimised", "desc": f"Handles {bank.get('date_format', 'date formats')} dates and {bank.get('column_layout', 'specific column layouts')}"},
                    {"title": f"Built for {uc['name'].lower()}", "desc": uc["benefit"]},
                    {"title": "Period coverage", "desc": f"Convert {uc.get('typical_timeframe', 'any period')} of {bank['name']} statements in minutes"},
                ],
                "features_heading": f"{bank['name']} + {uc['name']} Features",
                "steps": [
                    {"title": f"Upload your {bank['name']} statement", "desc": f"Drop your {bank['full_name']} statement PDF. Supports {', '.join(bank['formats'])}. Need {uc.get('typical_timeframe', 'multiple months')}? Upload them all."},
                    {"title": "AI extracts every transaction", "desc": f"Our AI handles {bank['name']}'s {bank.get('date_format', 'date format')} and {bank.get('column_layout', 'layout')} — extracting {uc.get('key_data_points', 'all transaction data')[:60] if uc.get('key_data_points') else 'all transaction data'}."},
                    {"title": f"Use for {uc['name'].lower()}", "desc": f"Download formatted Excel ready for {uc['name'].lower()}. {uc.get('formatting_requirements', 'Sort, filter, and analyse as needed.')}"},
                ],
                "faqs": [
                    {"q": f"Can I use a {bank['name']} statement for {uc['name'].lower()}?", "a": f"Yes. BankScan AI converts {bank['name']} PDFs to structured spreadsheets ideal for {uc['name'].lower()}. {uc['benefit']}"},
                    {"q": f"How many months of {bank['name']} statements do I need?", "a": f"For {uc['name'].lower()}, typically {uc.get('typical_timeframe', '3-6 months')} are required. {uc.get('required_by', 'The reviewing party')} will specify the exact period."},
                    {"q": f"How does BankScan AI handle {bank['name']}'s format?", "a": f"{bank['name']} uses {bank.get('date_format', 'specific dates')} with {bank.get('column_layout', 'a specific layout')}. BankScan AI is trained on this exact format."},
                    {"q": "Is my data secure?", "a": "Your bank statements are processed in memory and deleted immediately. We never store your financial data."},
                ],
                "cta_heading": f"Convert {bank['name']} for {uc['name']} — Fast",
                "cta_subtext": f"{uc.get('deadline_pressure', 'Convert bank statements in seconds, not hours.')}",
                "cta_text": f"Convert {bank['name']} Statement Free",
            }
    return pages


def _generate_software_profession_pages():
    """Generate /tools/{software}-for-{profession} pages for top combos."""
    pages = {}
    for sw_slug, sw in ACCOUNTING_SOFTWARE.items():
        for prof_slug, prof in PROFESSIONS.items():
            if not prof.get("combo_eligible"):
                continue
            slug = f"{sw_slug}-bank-import-for-{prof_slug}"

            import_guide = None
            if sw.get("import_steps"):
                steps = [s.strip() for s in sw["import_steps"].split(".") if s.strip()]
                import_guide = {
                    "heading": f"Import Steps for {prof['name']} Using {sw['name']}",
                    "intro": f"As {prof['singular'].lower() if prof['singular'][0].lower() not in 'aeiou' else 'an ' + prof['singular'].lower()}, here's how to get bank data into {sw['name']}:",
                    "steps": steps,
                    "common_errors": sw.get("common_import_errors", ""),
                }

            pages[slug] = {
                "type": "software_profession",
                "title": f"Import Bank Statements to {sw['name']} for {prof['name']} | BankScan AI",
                "h1": f"{sw['name']} Bank Statement Import for {prof['name']}",
                "meta_description": f"Convert bank statement PDFs to {sw['import_format']} for {sw['name']} import. Built for {prof['name'].lower()}. {prof.get('time_savings', 'All banks supported.')}",
                "keywords": f"{sw['name']} for {prof['name'].lower()}, {sw['name']} bank import {prof['name'].lower()}, {prof['name'].lower()} {sw['name']} statement tool",
                "software": sw,
                "software_slug": sw_slug,
                "profession": prof,
                "profession_slug": prof_slug,
                "intro_heading": f"{sw['name']} + {prof['name']}: Faster Bank Import",
                "intro": f"{prof['pain_point']} If you use {sw['name']} for your accounting, you need a fast way to get bank statement data into the system.",
                "solution_heading": f"BankScan AI for {prof['name']} Using {sw['name']}",
                "solution": f"BankScan AI converts any bank statement PDF to {sw['import_format']} formatted for direct import into {sw['name']}. {prof['benefit']}",
                "import_guide": import_guide,
                "workflow_section": {"heading": f"The {prof['name']} + {sw['name']} Workflow", "content": prof.get("workflow_detail", prof["pain_point"]), "jargon_terms": prof.get("industry_jargon", [])[:4]} if prof.get("workflow_detail") else None,
                "features": _sw_features(sw)[:2] + _prof_features(prof)[:2],
                "features_heading": f"{sw['name']} Features for {prof['name']}",
                "steps": [
                    {"title": "Upload any bank statement PDF", "desc": f"Supports all major banks. {prof.get('time_savings', 'Convert in seconds.')}"},
                    {"title": f"AI formats for {sw['name']}", "desc": f"Output as {sw['import_format']} with {sw.get('column_mapping', 'columns matching import requirements')} — dates in {sw.get('date_format_required', 'the correct format')}."},
                    {"title": f"Import and reconcile", "desc": f"{sw.get('import_steps', 'Upload and start reconciling.')} {sw.get('unique_integration', '')}"},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Is BankScan AI good for {prof['name'].lower()} using {sw['name']}?", "a": f"Yes. BankScan AI produces {sw['import_format']} files for {sw['name']} and is designed for {prof['name'].lower()}. {prof['benefit']}"},
                    {"q": f"What column format does {sw['name']} need?", "a": f"{sw.get('column_mapping', sw['name'] + ' requires specific column formatting.')} BankScan AI handles this automatically."},
                    {"q": f"What software do {prof['name'].lower()} typically pair with {sw['name']}?", "a": f"{prof['name']} commonly use {', '.join(prof.get('software_preferences', [sw['name']])[:3])}. BankScan AI is compatible with all of them."},
                    {"q": "Is there a free tier?", "a": "Yes. Try BankScan AI free with limited conversions per month. Paid plans from $9.99/month."},
                ],
                "cta_heading": f"{sw['name']} Import — Built for {prof['name']}",
                "cta_subtext": sw.get('market_position', f"The most popular way for {prof['name'].lower()} to import bank data into {sw['name']}."),
                "cta_text": f"Try Free — {sw['name']} + {prof['name']}",
            }
    return pages


def _generate_profession_usecase_pages():
    """Generate /tools/{profession}-bank-statement-for-{use-case} pages."""
    pages = {}
    combo_professions = {k: v for k, v in PROFESSIONS.items() if v.get("combo_eligible")}
    for prof_slug, prof in combo_professions.items():
        for uc_slug, uc in USE_CASES.items():
            slug = f"{prof_slug}-bank-statement-for-{uc_slug}"

            workflow_section = None
            if prof.get("workflow_detail"):
                workflow_section = {
                    "heading": f"How {prof['name']} Handle {uc['name']}",
                    "content": f"When preparing bank statements for {uc['name'].lower()}, {prof['name'].lower()} need to {prof['workflow_detail'][:150]}. BankScan AI automates the data extraction step.",
                    "jargon_terms": prof.get("industry_jargon", [])[:4],
                }

            compliance_section = None
            if prof.get("compliance_requirements") or uc.get("legal_basis"):
                key_points = []
                if prof.get("compliance_requirements"):
                    key_points.append(prof["compliance_requirements"] if isinstance(prof["compliance_requirements"], str) else prof["compliance_requirements"][0])
                if uc.get("legal_basis"):
                    key_points.append(f"Legal basis: {uc['legal_basis']}")
                compliance_section = {
                    "heading": f"Compliance: {prof['name']} + {uc['name']}",
                    "content": f"When {prof['name'].lower()} prepare bank statements for {uc['name'].lower()}, specific compliance requirements apply.",
                    "key_points": key_points,
                }

            pages[slug] = {
                "type": "profession_usecase",
                "title": f"Bank Statement Converter for {prof['name']} — {uc['name']} | BankScan AI",
                "h1": f"Bank Statement Converter for {prof['name']}: {uc['name']}",
                "meta_description": f"Convert bank statements for {uc['name'].lower()} as {prof['singular'].lower() if not prof['singular'][0].lower() in 'aeiou' else 'an ' + prof['singular'].lower()}. {uc.get('deadline_pressure', 'AI-powered, all banks, instant results.')}",
                "keywords": f"{prof['name'].lower()} bank statement {uc['name'].lower()}, {prof['name'].lower()} {uc_slug} statement tool, {uc['name'].lower()} for {prof['name'].lower()}",
                "profession": prof,
                "profession_slug": prof_slug,
                "use_case": uc,
                "use_case_slug": uc_slug,
                "intro_heading": f"{prof['name']} + {uc['name']}: The Challenge",
                "intro": f"As {prof['singular'].lower() if prof['singular'][0].lower() not in 'aeiou' else 'an ' + prof['singular'].lower()}, preparing bank statements for {uc['name'].lower()} is a common but time-consuming task. {uc['pain_point']}",
                "solution_heading": f"How BankScan AI Helps {prof['name']} with {uc['name']}",
                "solution": f"{prof['benefit']} For {uc['name'].lower()} specifically, BankScan AI extracts {uc.get('key_data_points', 'all relevant data')[:80] if uc.get('key_data_points') else 'all relevant data'} automatically.",
                "workflow_section": workflow_section,
                "compliance_section": compliance_section,
                "features": _prof_features(prof)[:2] + _uc_features(uc)[:2],
                "features_heading": f"Features for {prof['name']} — {uc['name']}",
                "steps": [
                    {"title": "Upload bank statement PDFs", "desc": f"Supports all major banks. Upload {uc.get('typical_timeframe', 'the required period')} of statements for {uc['name'].lower()}."},
                    {"title": "AI extracts what you need", "desc": f"Our AI extracts {uc.get('key_data_points', 'transaction data')[:60] if uc.get('key_data_points') else 'transaction data'} — formatted for {prof['name'].lower()} workflows."},
                    {"title": f"Complete your {uc['name'].lower()} work", "desc": f"Download Excel files ready for {uc['name'].lower()}. {uc.get('formatting_requirements', 'Import into your preferred software or share with clients.')}"},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Can {prof['name'].lower()} use BankScan AI for {uc['name'].lower()}?", "a": f"Yes. BankScan AI is built for {prof['name'].lower()} handling {uc['name'].lower()} work. {prof['benefit']}"},
                    {"q": f"How many months of statements for {uc['name'].lower()}?", "a": f"Typically {uc.get('typical_timeframe', '3-6 months')} are required. {uc.get('required_by', 'The reviewing party')} will specify the exact period."},
                    {"q": f"What software do {prof['name'].lower()} use for this?", "a": f"{prof['name']} commonly use {', '.join(prof.get('software_preferences', ['Excel', 'Xero', 'QuickBooks'])[:3])} for {uc['name'].lower()} work. BankScan AI output is compatible with all."},
                    {"q": "Is there a free tier?", "a": "Yes. Try free with limited conversions. Paid plans from $9.99/month."},
                ],
                "cta_heading": f"{uc['name']} — Built for {prof['name']}",
                "cta_subtext": f"{uc.get('deadline_pressure', 'Convert bank statements in seconds, not hours.')}",
                "cta_text": f"Try Free — {prof['name']} + {uc['name']}",
            }
    return pages


def _generate_software_usecase_pages():
    """Generate /tools/{software}-import-for-{use-case} pages."""
    pages = {}
    for sw_slug, sw in ACCOUNTING_SOFTWARE.items():
        for uc_slug, uc in USE_CASES.items():
            slug = f"{sw_slug}-import-for-{uc_slug}"

            import_guide = None
            if sw.get("import_steps"):
                steps = [s.strip() for s in sw["import_steps"].split(".") if s.strip()]
                import_guide = {
                    "heading": f"Import for {uc['name']} into {sw['name']}",
                    "intro": f"After converting your bank statements for {uc['name'].lower()}:",
                    "steps": steps,
                    "common_errors": sw.get("common_import_errors", ""),
                }

            pages[slug] = {
                "type": "software_usecase",
                "title": f"Import Bank Statement to {sw['name']} for {uc['name']} | BankScan AI",
                "h1": f"Import Bank Statements into {sw['name']} for {uc['name']}",
                "meta_description": f"Convert bank statement PDFs for {uc['name'].lower()} and import into {sw['name']}. {sw.get('market_position', 'AI-powered converter.')} All banks supported.",
                "keywords": f"{sw['name']} {uc['name'].lower()}, import bank statement {sw['name']} {uc['name'].lower()}, {uc_slug} {sw['name']} tool",
                "software": sw,
                "software_slug": sw_slug,
                "use_case": uc,
                "use_case_slug": uc_slug,
                "intro_heading": f"{sw['name']} + {uc['name']}: Complete Workflow",
                "intro": f"Preparing for {uc['name'].lower()} and using {sw['name']}? {uc['pain_point']} BankScan AI bridges the gap between your bank's PDF statements and {sw['name']}'s import feature.",
                "solution_heading": f"BankScan AI → {sw['name']} for {uc['name']}",
                "solution": f"Convert bank statement PDFs to {sw['import_format']} formatted for {sw['name']} import. {uc['benefit']}",
                "import_guide": import_guide,
                "features": _sw_features(sw)[:2] + _uc_features(uc)[:1],
                "features_heading": f"{sw['name']} + {uc['name']} Features",
                "steps": [
                    {"title": "Upload your bank statement PDF", "desc": f"Supports all major banks. Upload {uc.get('typical_timeframe', 'the required period')} of statements."},
                    {"title": f"AI formats for {sw['name']}", "desc": f"Output as {sw['import_format']} with {sw.get('column_mapping', 'columns matching import requirements')} — dates in {sw.get('date_format_required', 'the correct format')}."},
                    {"title": f"Import and use for {uc['name'].lower()}", "desc": f"Upload into {sw['name']} and use the reconciled data for {uc['name'].lower()}."},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Can I import bank statements into {sw['name']} for {uc['name'].lower()}?", "a": f"Yes. BankScan AI converts PDFs to {sw['import_format']} for {sw['name']}. {uc['benefit']}"},
                    {"q": f"What format does {sw['name']} need?", "a": f"{sw['name']} requires {sw.get('column_mapping', sw['import_format'] + ' format')} with dates in {sw.get('date_format_required', 'the correct format')}. BankScan AI handles this automatically."},
                    {"q": f"How many months for {uc['name'].lower()}?", "a": f"Typically {uc.get('typical_timeframe', '3-6 months')}. {uc.get('required_by', 'The reviewing party')} will specify the exact period."},
                    {"q": "Is there a free tier?", "a": "Yes. Try free with limited conversions. Paid plans from $9.99/month."},
                ],
                "cta_heading": f"{sw['name']} Import for {uc['name']}",
                "cta_subtext": f"{uc.get('deadline_pressure', 'Convert and import in minutes, not hours.')}",
                "cta_text": f"Convert for {sw['name']} + {uc['name']}",
            }
    return pages


def _generate_bank_profession_format_pages():
    """Generate /tools/{bank}-to-{format}-for-{profession} triple-combo pages."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        for prof_slug, prof in PROFESSIONS.items():
            if not prof.get("combo_eligible"):
                continue
            for fmt_slug, fmt in FORMATS.items():
                slug = f"{bank_slug}-to-{fmt_slug}-for-{prof_slug}"
                pages[slug] = {
                    "type": "bank_profession_format",
                    "title": f"Convert {bank['name']} to {fmt['name']} for {prof['name']} | BankScan AI",
                    "h1": f"{bank['name']} Statement to {fmt['name']} for {prof['name']}",
                    "meta_description": f"Convert {bank['name']} PDFs ({bank.get('date_format', 'all formats')}) to {fmt['name']} for {prof['name'].lower()}. {fmt.get('best_for', 'AI-powered, accurate, fast.')}",
                    "keywords": f"{bank['name']} to {fmt['name']} for {prof['name'].lower()}, {bank['name']} {fmt_slug} converter {prof_slug}, {bank_slug} statement {fmt_slug} {prof_slug}",
                    "bank": bank,
                    "bank_slug": bank_slug,
                    "profession": prof,
                    "profession_slug": prof_slug,
                    "format": fmt,
                    "format_slug": fmt_slug,
                    "intro_heading": f"{bank['name']} → {fmt['name']} for {prof['name']}",
                    "intro": f"As {prof['singular'].lower() if prof['singular'][0].lower() not in 'aeiou' else 'an ' + prof['singular'].lower()}, you need {bank['name']} bank statements in {fmt['name']} format. {bank.get('statement_notes', '')} BankScan AI handles the conversion — {bank.get('date_format', 'date formats')}, {bank.get('column_layout', 'column layouts')}, and all.",
                    "features": [
                        {"title": f"{bank['name']} expertise", "desc": f"Handles {bank.get('date_format', 'specific dates')} and {bank.get('column_layout', 'unique layouts')}"},
                        {"title": f"{fmt['name']} output", "desc": fmt.get("unique_advantages", f"Clean {fmt['name']} with all transaction data")},
                        {"title": f"Built for {prof['name'].lower()}", "desc": prof.get("time_savings", prof["benefit"])},
                    ],
                    "features_heading": f"{bank['name']} + {fmt['name']} + {prof['name']}",
                    "steps": [
                        {"title": f"Upload {bank['name']} PDF", "desc": f"Drop your {bank['name']} statement PDF. Supports {', '.join(bank['formats'])}."},
                        {"title": f"AI converts to {fmt['name']}", "desc": f"Parses {bank['name']}'s {bank.get('date_format', 'format')}, extracts all transactions, outputs {fmt['name']} ({fmt['extension'] or 'cloud format'})."},
                        {"title": f"Use in your {prof['name'].lower()} work", "desc": f"Download and use with {', '.join(prof.get('software_preferences', ['your tools'])[:2]) if prof.get('software_preferences') else 'your tools'}. {prof.get('benefit', '')}"},
                    ],
                    "faqs": [
                        {"q": f"Can I convert {bank['name']} to {fmt['name']}?", "a": f"Yes. BankScan AI converts {bank['name']} PDFs to {fmt['name']} with {bank.get('date_format', 'correct dates')} and all transaction details preserved. {fmt.get('technical_notes', '')}"},
                        {"q": f"Is {fmt['name']} right for {prof['name'].lower()}?", "a": f"{fmt.get('best_for', fmt['name'] + ' is widely used by professionals.')} {prof['name']} particularly benefit from {fmt.get('unique_advantages', 'the clean output format')}."},
                        {"q": "How accurate is the conversion?", "a": f"BankScan AI achieves 99%+ accuracy on {bank['name']} statements, handling {bank.get('common_issues', 'complex formatting')} automatically."},
                    ],
                    "cta_heading": f"{bank['name']} to {fmt['name']} — For {prof['name']}",
                    "cta_subtext": f"Handles {bank.get('date_format', 'all date formats')}, outputs perfect {fmt['name']}. Free tier available.",
                    "cta_text": f"Convert {bank['name']} to {fmt['name']} Free",
                }
    return pages


def _generate_profession_format_pages():
    """Generate /tools/{format}-converter-for-{profession} pages."""
    pages = {}
    for prof_slug, prof in PROFESSIONS.items():
        if not prof.get("combo_eligible"):
            continue
        for fmt_slug, fmt in FORMATS.items():
            slug = f"{fmt_slug}-converter-for-{prof_slug}"
            pages[slug] = {
                "type": "profession_format",
                "title": f"Bank Statement to {fmt['name']} Converter for {prof['name']} | BankScan AI",
                "h1": f"Bank Statement to {fmt['name']} for {prof['name']}",
                "meta_description": f"Convert any bank statement PDF to {fmt['name']} for {prof['name'].lower()}. {fmt.get('best_for', 'AI-powered converter supporting 40+ banks.')}",
                "keywords": f"bank statement to {fmt['name']} for {prof['name'].lower()}, {fmt_slug} converter {prof_slug}, {prof_slug} bank statement {fmt_slug}",
                "profession": prof,
                "profession_slug": prof_slug,
                "format": fmt,
                "format_slug": fmt_slug,
                "intro_heading": f"Why {prof['name']} Need {fmt['name']} Format",
                "intro": f"As {prof['singular'].lower() if prof['singular'][0].lower() not in 'aeiou' else 'an ' + prof['singular'].lower()}, converting bank statements to {fmt['name']} saves hours of manual data entry. {fmt.get('description', 'Get clean output files.')} {prof.get('time_savings', '')}",
                "features": [
                    {"title": f"{fmt['name']} advantages", "desc": fmt.get("unique_advantages", f"Clean {fmt['name']} output with all data preserved")},
                    {"title": f"Built for {prof['name'].lower()}", "desc": prof.get("time_savings", prof["benefit"])},
                    {"title": "Format compatibility", "desc": fmt.get('best_for', fmt['name'] + ' works with most professional software')},
                ],
                "features_heading": f"{fmt['name']} Features for {prof['name']}",
                "pro_tip": {"title": f"{fmt['name']} Tip for {prof['name']}", "content": fmt.get("technical_notes", f"{fmt['name']} output preserves all transaction data for professional use.")} if fmt.get("technical_notes") else None,
                "steps": [
                    {"title": "Upload any bank statement PDF", "desc": f"Supports all major banks. {prof.get('time_savings', 'Convert in seconds, not hours.')}"},
                    {"title": f"AI outputs {fmt['name']}", "desc": f"Our AI extracts all transactions and outputs {fmt['name']} ({fmt['extension'] or 'cloud format'}). {fmt.get('technical_notes', '')[:80] if fmt.get('technical_notes') else ''}"},
                    {"title": f"Use in your {prof['name'].lower()} work", "desc": f"Import into {', '.join(prof.get('software_preferences', ['your tools'])[:3]) if prof.get('software_preferences') else 'your preferred software'} or use for direct analysis."},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Can I convert bank statements to {fmt['name']}?", "a": f"Yes. BankScan AI converts any bank statement PDF to {fmt['name']} with 99%+ accuracy. {fmt.get('description', '')}"},
                    {"q": f"Is {fmt['name']} the right format for {prof['name'].lower()}?", "a": f"{fmt.get('best_for', fmt['name'] + ' is widely used by professionals.')} {prof['name']} use {fmt['name']} for {', '.join(prof.get('software_preferences', ['various tools'])[:2]) if prof.get('software_preferences') else 'various tools'} workflows."},
                    {"q": f"What are {fmt['name']}'s limitations?", "a": fmt.get("limitations", f"{fmt['name']} works well for most professional use cases.")},
                ],
                "cta_heading": f"{fmt['name']} Converter — Built for {prof['name']}",
                "cta_subtext": f"{fmt.get('best_for', 'Fast, accurate conversion with a free tier.')}",
                "cta_text": f"Convert to {fmt['name']} Free",
            }
    return pages



def build_all_seo_pages():
    """Build and return the complete dictionary of all programmatic SEO pages."""
    pages = {}
    pages.update(_generate_bank_pages())
    pages.update(_generate_profession_pages())
    pages.update(_generate_receipt_pages())
    pages.update(_generate_bank_format_pages())
    pages.update(_generate_software_pages())
    pages.update(_generate_use_case_pages())
    pages.update(_generate_bank_profession_combo_pages())
    pages.update(_generate_bank_software_pages())
    pages.update(_generate_bank_usecase_pages())
    pages.update(_generate_software_profession_pages())
    pages.update(_generate_profession_usecase_pages())
    pages.update(_generate_software_usecase_pages())
    pages.update(_generate_bank_profession_format_pages())
    pages.update(_generate_profession_format_pages())
    return pages


# Pre-built at import time for fast lookups
SEO_PAGES = build_all_seo_pages()
