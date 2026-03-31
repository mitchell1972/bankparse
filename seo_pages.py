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
    },
    "barclays": {
        "name": "Barclays",
        "full_name": "Barclays Bank UK",
        "statement_notes": "Barclays statements come in a clean tabular layout, but date formats and running balances can trip up basic parsers.",
        "formats": ["PDF", "CSV"],
        "popular": True,
    },
    "lloyds": {
        "name": "Lloyds",
        "full_name": "Lloyds Banking Group",
        "statement_notes": "Lloyds PDF statements use a consistent column layout, but multi-page statements sometimes split transactions across page breaks.",
        "formats": ["PDF"],
        "popular": True,
    },
    "natwest": {
        "name": "NatWest",
        "full_name": "NatWest Bank",
        "statement_notes": "NatWest statements include detailed transaction references that can cause column misalignment in generic converters.",
        "formats": ["PDF"],
        "popular": True,
    },
    "monzo": {
        "name": "Monzo",
        "full_name": "Monzo Bank",
        "statement_notes": "Monzo provides CSV exports natively, but many clients still send PDF statements that need parsing for bookkeeping software.",
        "formats": ["PDF", "CSV"],
        "popular": True,
    },
    "starling": {
        "name": "Starling",
        "full_name": "Starling Bank",
        "statement_notes": "Starling Bank statements have a modern layout with merchant categories, which BankScan AI preserves during conversion.",
        "formats": ["PDF", "CSV"],
        "popular": False,
    },
    "santander": {
        "name": "Santander",
        "full_name": "Santander UK",
        "statement_notes": "Santander UK statements use a two-column debit/credit layout that requires careful parsing to maintain accuracy.",
        "formats": ["PDF"],
        "popular": True,
    },
    "nationwide": {
        "name": "Nationwide",
        "full_name": "Nationwide Building Society",
        "statement_notes": "Nationwide statements include both current account and savings in a single PDF, which BankScan AI separates automatically.",
        "formats": ["PDF"],
        "popular": False,
    },
    "rbs": {
        "name": "RBS",
        "full_name": "Royal Bank of Scotland",
        "statement_notes": "RBS statements share a similar format to NatWest. BankScan AI handles both with the same high accuracy.",
        "formats": ["PDF"],
        "popular": False,
    },
    "halifax": {
        "name": "Halifax",
        "full_name": "Halifax (Bank of Scotland)",
        "statement_notes": "Halifax statements use a Lloyds-family format with slight variations in header layout that BankScan AI automatically detects.",
        "formats": ["PDF"],
        "popular": False,
    },
    "revolut": {
        "name": "Revolut",
        "full_name": "Revolut",
        "statement_notes": "Revolut statements include multi-currency transactions. BankScan AI preserves the original currency and exchange rate columns.",
        "formats": ["PDF", "CSV"],
        "popular": True,
    },
    "tide": {
        "name": "Tide",
        "full_name": "Tide Business Banking",
        "statement_notes": "Tide business account statements include invoice references and categories that BankScan AI maps to your bookkeeping codes.",
        "formats": ["PDF", "CSV"],
        "popular": False,
    },
    "metro-bank": {
        "name": "Metro Bank",
        "full_name": "Metro Bank",
        "statement_notes": "Metro Bank statements use a straightforward layout. BankScan AI converts them cleanly with full transaction detail preserved.",
        "formats": ["PDF"],
        "popular": False,
    },
    "tsb": {
        "name": "TSB",
        "full_name": "TSB Bank",
        "statement_notes": "TSB statements are formatted similarly to Lloyds. BankScan AI recognises the layout and applies the correct parsing rules.",
        "formats": ["PDF"],
        "popular": False,
    },
    "coutts": {
        "name": "Coutts",
        "full_name": "Coutts & Co",
        "statement_notes": "Coutts private banking statements have a premium layout with additional wealth summary sections that BankScan AI filters correctly.",
        "formats": ["PDF"],
        "popular": False,
    },
    "virgin-money": {
        "name": "Virgin Money",
        "full_name": "Virgin Money UK",
        "statement_notes": "Virgin Money statements (formerly Clydesdale/Yorkshire Bank) use a modern layout with transaction categories that BankScan AI preserves during conversion.",
        "formats": ["PDF"],
        "popular": False,
    },
    "first-direct": {
        "name": "First Direct",
        "full_name": "First Direct (HSBC)",
        "statement_notes": "First Direct statements follow an HSBC-family format but with a distinct minimalist layout. BankScan AI detects the variant automatically.",
        "formats": ["PDF"],
        "popular": False,
    },
    "co-op-bank": {
        "name": "Co-operative Bank",
        "full_name": "The Co-operative Bank",
        "statement_notes": "Co-operative Bank statements use a traditional tabular layout with running balances. BankScan AI handles the format reliably, including joint account statements.",
        "formats": ["PDF"],
        "popular": False,
    },
    "bank-of-ireland": {
        "name": "Bank of Ireland",
        "full_name": "Bank of Ireland UK",
        "statement_notes": "Bank of Ireland UK statements use a layout common to Irish banking formats. BankScan AI parses both GB and NI account statement variants.",
        "formats": ["PDF"],
        "popular": False,
    },
    "danske-bank": {
        "name": "Danske Bank",
        "full_name": "Danske Bank UK",
        "statement_notes": "Danske Bank UK (Northern Ireland) statements use a Scandinavian-influenced layout. BankScan AI handles the unique date and amount formatting.",
        "formats": ["PDF"],
        "popular": False,
    },
    "aib": {
        "name": "AIB",
        "full_name": "AIB UK (Allied Irish Banks)",
        "statement_notes": "AIB UK statements serve Northern Ireland customers with a format that differs from mainland UK banks. BankScan AI parses both GBP and EUR transactions.",
        "formats": ["PDF"],
        "popular": False,
    },
    "investec": {
        "name": "Investec",
        "full_name": "Investec Bank UK",
        "statement_notes": "Investec private banking statements include investment account summaries alongside current account transactions. BankScan AI separates and converts the banking transactions.",
        "formats": ["PDF"],
        "popular": False,
    },
    "handelsbanken": {
        "name": "Handelsbanken",
        "full_name": "Handelsbanken UK",
        "statement_notes": "Handelsbanken's relationship banking model means each branch produces slightly different statement layouts. BankScan AI adapts to all Handelsbanken UK variants.",
        "formats": ["PDF"],
        "popular": False,
    },
    "atom-bank": {
        "name": "Atom Bank",
        "full_name": "Atom Bank",
        "statement_notes": "Atom Bank, the UK's first app-only bank, produces digital-first PDF statements with a clean layout that BankScan AI converts with high accuracy.",
        "formats": ["PDF"],
        "popular": False,
    },
    "chase-uk": {
        "name": "Chase UK",
        "full_name": "Chase (J.P. Morgan UK)",
        "statement_notes": "Chase UK statements use a modern, minimal layout with merchant categories and cashback details. BankScan AI extracts all transaction data including rewards information.",
        "formats": ["PDF"],
        "popular": False,
    },
    "wise": {
        "name": "Wise",
        "full_name": "Wise (formerly TransferWise)",
        "statement_notes": "Wise multi-currency statements include transactions in multiple currencies with exchange rates. BankScan AI preserves currency codes, rates, and fee breakdowns.",
        "formats": ["PDF", "CSV"],
        "popular": False,
    },
    "paypal": {
        "name": "PayPal",
        "full_name": "PayPal UK",
        "statement_notes": "PayPal statements include a mix of payments, refunds, fees, and currency conversions. BankScan AI separates transaction types and preserves fee details.",
        "formats": ["PDF", "CSV"],
        "popular": False,
    },
    "stripe": {
        "name": "Stripe",
        "full_name": "Stripe Payments UK",
        "statement_notes": "Stripe payout statements list individual charges, refunds, and fees per payout batch. BankScan AI expands payout summaries into line-by-line transaction detail.",
        "formats": ["PDF", "CSV"],
        "popular": False,
    },
    "amex": {
        "name": "American Express",
        "full_name": "American Express UK",
        "statement_notes": "American Express statements include card transactions, membership rewards, and payment summaries. BankScan AI extracts all transaction detail including merchant categories.",
        "formats": ["PDF"],
        "popular": False,
    },
    "credit-card": {
        "name": "Credit Card",
        "full_name": "UK Credit Card Statements",
        "statement_notes": "Credit card statements from any UK issuer — including Barclaycard, MBNA, Capital One, Tesco Bank, and John Lewis — are parsed by BankScan AI with high accuracy.",
        "formats": ["PDF", "scanned PDF"],
        "popular": False,
    },
    "barclaycard": {
        "name": "Barclaycard",
        "full_name": "Barclaycard",
        "statement_notes": "Barclaycard statements list purchases, payments, interest charges, and rewards in a multi-section layout. BankScan AI extracts all transaction rows into a clean spreadsheet.",
        "formats": ["PDF"],
        "popular": False,
    },
    "john-lewis": {
        "name": "John Lewis Finance",
        "full_name": "John Lewis Partnership Card",
        "statement_notes": "John Lewis credit card statements include partnership reward points alongside transactions. BankScan AI extracts the financial data while preserving reward details.",
        "formats": ["PDF"],
        "popular": False,
    },
    "tesco-bank": {
        "name": "Tesco Bank",
        "full_name": "Tesco Bank",
        "statement_notes": "Tesco Bank current account and credit card statements use a distinct layout with Clubcard points information. BankScan AI extracts all financial transaction data.",
        "formats": ["PDF"],
        "popular": False,
    },
    "n26": {
        "name": "N26",
        "full_name": "N26 (UK)",
        "statement_notes": "N26 digital bank statements use a modern format with IBAN references and categorised spending. BankScan AI handles the European-style date and amount formatting.",
        "formats": ["PDF", "CSV"],
        "popular": False,
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
    },
    "bookkeepers": {
        "name": "Bookkeepers",
        "singular": "Bookkeeper",
        "pain_point": "Bookkeepers deal with statements from dozens of different banks, each with its own PDF format. Copy-pasting transactions is error-prone and tedious.",
        "benefit": "BankScan AI handles every major UK bank format automatically. Upload the PDF, download the spreadsheet — no manual data entry needed.",
        "keywords": ["bookkeeper bank statement converter", "bookkeeping PDF tool", "bookkeeper statement to Excel"],
        "combo_eligible": True,
    },
    "solicitors": {
        "name": "Solicitors",
        "singular": "Solicitor",
        "pain_point": "Solicitors reviewing financial disclosure in divorce, fraud, or commercial cases need to analyse bank statements quickly and accurately.",
        "benefit": "Convert client and third-party bank statements to searchable, sortable Excel spreadsheets for faster case analysis and evidence preparation.",
        "keywords": ["solicitor bank statement tool", "legal statement converter", "financial disclosure tool"],
        "combo_eligible": True,
    },
    "small-business-owners": {
        "name": "Small Business Owners",
        "singular": "Small Business Owner",
        "pain_point": "Small business owners often need to reconcile bank statements with invoices and expenses but lack accounting software that imports PDFs directly.",
        "benefit": "Upload your bank statement PDF and get a clean spreadsheet you can use to track cash flow, reconcile invoices, or send to your accountant.",
        "keywords": ["small business statement converter", "SME bank statement tool", "business PDF to Excel"],
        "combo_eligible": True,
    },
    "tax-advisors": {
        "name": "Tax Advisors",
        "singular": "Tax Advisor",
        "pain_point": "Tax advisors need to review multiple years of bank statements during tax investigations or self-assessment preparation.",
        "benefit": "Batch-convert entire folders of bank statement PDFs into structured spreadsheets to speed up tax return preparation and HMRC enquiry responses.",
        "keywords": ["tax advisor statement tool", "HMRC statement converter", "tax return bank statement"],
        "combo_eligible": True,
    },
    "mortgage-brokers": {
        "name": "Mortgage Brokers",
        "singular": "Mortgage Broker",
        "pain_point": "Mortgage brokers need to verify income and spending from bank statements when assessing affordability for lender applications.",
        "benefit": "Instantly convert applicant bank statements to Excel to verify income, identify regular commitments, and prepare affordability summaries.",
        "keywords": ["mortgage broker statement tool", "affordability check tool", "mortgage statement converter"],
        "combo_eligible": True,
    },
    "forensic-accountants": {
        "name": "Forensic Accountants",
        "singular": "Forensic Accountant",
        "pain_point": "Forensic accountants investigating fraud or financial irregularities need to process hundreds of bank statements into analysable data.",
        "benefit": "Bulk-convert bank statements into structured spreadsheets for pattern analysis, timeline reconstruction, and expert witness reporting.",
        "keywords": ["forensic accounting tool", "fraud investigation statement converter", "financial investigation software"],
        "combo_eligible": True,
    },
    "landlords": {
        "name": "Landlords",
        "singular": "Landlord",
        "pain_point": "Landlords managing multiple properties need to track rental income and expenses from bank statements for tax returns.",
        "benefit": "Convert your bank statements to Excel to easily categorise rental income, maintenance costs, and mortgage payments for self-assessment.",
        "keywords": ["landlord bank statement tool", "rental income tracker", "property expense converter"],
        "combo_eligible": True,
    },
    "charities": {
        "name": "Charities & Non-Profits",
        "singular": "Charity",
        "pain_point": "Charities need accurate financial records for trustees, donors, and the Charity Commission — but often rely on volunteers without accounting expertise.",
        "benefit": "Make bank statement reconciliation simple for volunteer treasurers. Upload statements, get clean spreadsheets for your financial reports.",
        "keywords": ["charity bank statement tool", "non-profit statement converter", "charity treasurer tool"],
        "combo_eligible": True,
    },
    "contractors": {
        "name": "Contractors & Freelancers",
        "singular": "Contractor",
        "pain_point": "Contractors and freelancers juggling personal and business accounts need to separate expenses for tax purposes.",
        "benefit": "Upload your bank statements and get organised spreadsheets that make expense categorisation and self-assessment tax returns straightforward.",
        "keywords": ["contractor bank statement tool", "freelancer statement converter", "IR35 expense tracker"],
        "combo_eligible": True,
    },
    "dentists": {
        "name": "Dentists",
        "singular": "Dentist",
        "pain_point": "Dental practices handle a mix of NHS and private payments, equipment purchases, and supplier invoices — all flowing through multiple bank accounts.",
        "benefit": "Convert your practice bank statements to Excel instantly, making it easy for your accountant to reconcile NHS payments, private fees, and practice expenses.",
        "keywords": ["dentist bank statement tool", "dental practice accounting", "NHS payment reconciliation"],
        "combo_eligible": True,
    },
    "doctors": {
        "name": "Doctors & GPs",
        "singular": "Doctor",
        "pain_point": "GP practices and private doctors deal with NHS reimbursements, private patient fees, and practice expenses across multiple accounts.",
        "benefit": "Automate bank statement conversion for your practice accounts so your accountant can quickly reconcile NHS payments and private income.",
        "keywords": ["doctor bank statement tool", "GP practice accounting", "medical practice finance"],
        "combo_eligible": True,
    },
    "estate-agents": {
        "name": "Estate Agents",
        "singular": "Estate Agent",
        "pain_point": "Estate agents manage client money accounts, commission income, and office expenses — all requiring careful reconciliation for compliance.",
        "benefit": "Convert your client money and office account statements to structured spreadsheets for faster reconciliation and compliance reporting.",
        "keywords": ["estate agent statement tool", "client money reconciliation", "property agent accounting"],
        "combo_eligible": True,
    },
    "restaurants": {
        "name": "Restaurants & Hospitality",
        "singular": "Restaurant Owner",
        "pain_point": "Restaurants process hundreds of card transactions daily, plus supplier payments. Reconciling bank statements with POS data is a major headache.",
        "benefit": "Upload your restaurant bank statements and get clean spreadsheets to reconcile against your POS system, suppliers, and HMRC VAT returns.",
        "keywords": ["restaurant bank statement tool", "hospitality accounting", "POS reconciliation tool"],
        "combo_eligible": True,
    },
    # --- New professions to reach 250+ pages ---
    "construction": {
        "name": "Construction Companies",
        "singular": "Construction Business",
        "pain_point": "Construction firms juggle CIS deductions, subcontractor payments, material costs, and retention payments — all needing accurate bank reconciliation for HMRC.",
        "benefit": "Convert your bank statements to Excel to quickly match CIS payments, subcontractor invoices, and material costs for VAT returns and CIS submissions.",
        "keywords": ["construction bank statement tool", "CIS statement converter", "builder accounting tool"],
        "combo_eligible": True,
    },
    "ecommerce": {
        "name": "E-commerce Sellers",
        "singular": "E-commerce Seller",
        "pain_point": "E-commerce sellers receive payments from Amazon, eBay, Shopify, and PayPal — but bank statements lump these together, making reconciliation painful.",
        "benefit": "Convert your bank statements to structured spreadsheets so you can match marketplace payouts, refunds, and fees against your sales records.",
        "keywords": ["ecommerce bank statement tool", "Amazon seller accounting", "Shopify statement converter"],
        "combo_eligible": True,
    },
    "financial-advisors": {
        "name": "Financial Advisors (IFAs)",
        "singular": "Financial Advisor",
        "pain_point": "Independent financial advisors reviewing client finances need to analyse bank statements to understand spending patterns, income, and savings capacity.",
        "benefit": "Convert client bank statements to Excel for quick financial analysis, cashflow modelling, and wealth planning presentations.",
        "keywords": ["IFA bank statement tool", "financial advisor statement converter", "wealth planning statement tool"],
        "combo_eligible": True,
    },
    "insolvency-practitioners": {
        "name": "Insolvency Practitioners",
        "singular": "Insolvency Practitioner",
        "pain_point": "Insolvency practitioners must review years of bank statements to trace transactions, identify preferences, and report to creditors.",
        "benefit": "Batch-convert years of bank statements into searchable spreadsheets for transaction tracing, preference analysis, and creditor reporting.",
        "keywords": ["insolvency practitioner tool", "IP statement converter", "creditor report statement tool"],
        "combo_eligible": False,
    },
    "payroll-managers": {
        "name": "Payroll Managers",
        "singular": "Payroll Manager",
        "pain_point": "Payroll managers need to verify salary payments, PAYE deductions, and pension contributions against bank statements every month.",
        "benefit": "Convert your company bank statements to Excel and cross-reference salary payments, HMRC PAYE, and pension fund transfers in minutes.",
        "keywords": ["payroll bank statement tool", "PAYE reconciliation", "payroll statement converter"],
        "combo_eligible": False,
    },
    "vat-consultants": {
        "name": "VAT Consultants",
        "singular": "VAT Consultant",
        "pain_point": "VAT consultants need to reconcile bank transactions against invoices and receipts to prepare accurate VAT returns and handle HMRC inspections.",
        "benefit": "Convert bank statements and receipts to structured spreadsheets to speed up VAT return preparation and build an audit-ready paper trail.",
        "keywords": ["VAT consultant statement tool", "VAT return bank statement", "Making Tax Digital statement tool"],
        "combo_eligible": False,
    },
    "letting-agents": {
        "name": "Letting Agents",
        "singular": "Letting Agent",
        "pain_point": "Letting agents managing tenant deposits, rent collection, and landlord payments across client money accounts need meticulous reconciliation.",
        "benefit": "Convert your client money account statements to Excel for fast reconciliation of rent receipts, deposit transfers, and management fee deductions.",
        "keywords": ["letting agent statement tool", "rent reconciliation", "client money account converter"],
        "combo_eligible": True,
    },
    "startups": {
        "name": "Startups & Founders",
        "singular": "Startup Founder",
        "pain_point": "Startup founders need to prepare financial summaries for investors, track burn rate, and reconcile multiple accounts — often without a dedicated finance team.",
        "benefit": "Convert your startup bank statements to clean spreadsheets for investor reporting, burn rate analysis, and quick reconciliation without hiring a bookkeeper.",
        "keywords": ["startup bank statement tool", "founder accounting tool", "burn rate tracker"],
        "combo_eligible": True,
    },
    "pharmacies": {
        "name": "Pharmacies",
        "singular": "Pharmacy Owner",
        "pain_point": "Pharmacies receive NHS reimbursements, prescription payments, and retail sales through multiple channels — making bank reconciliation complex.",
        "benefit": "Convert your pharmacy bank statements to Excel to reconcile NHS payments, wholesaler invoices, and retail takings quickly and accurately.",
        "keywords": ["pharmacy bank statement tool", "NHS pharmacy payments", "pharmacy accounting"],
        "combo_eligible": True,
    },
    "nurseries": {
        "name": "Nurseries & Childcare",
        "singular": "Nursery Owner",
        "pain_point": "Nurseries manage parent fee payments, government funding allocations, staff wages, and supplier costs — often across multiple accounts.",
        "benefit": "Convert bank statements to spreadsheets to reconcile parent payments against invoices, track government childcare funding, and manage supplier costs.",
        "keywords": ["nursery bank statement tool", "childcare accounting", "nursery fee reconciliation"],
        "combo_eligible": True,
    },
    "veterinarians": {
        "name": "Veterinarians",
        "singular": "Vet Practice Owner",
        "pain_point": "Veterinary practices handle client payments, insurance claims, pharmaceutical purchases, and equipment finance — all needing accurate reconciliation.",
        "benefit": "Convert your practice bank statements to Excel to match client payments, reconcile insurance reimbursements, and track pharmaceutical costs.",
        "keywords": ["vet practice bank statement", "veterinary accounting tool", "vet practice finance"],
        "combo_eligible": True,
    },
    "architects": {
        "name": "Architects",
        "singular": "Architect",
        "pain_point": "Architects bill in stages against project milestones, manage professional indemnity costs, and track expenses across multiple projects simultaneously.",
        "benefit": "Convert your bank statements to structured spreadsheets to match stage payments against projects, track professional fees, and prepare for your accountant.",
        "keywords": ["architect bank statement tool", "architecture practice accounting", "project billing reconciliation"],
        "combo_eligible": True,
    },
    "photographers": {
        "name": "Photographers & Videographers",
        "singular": "Photographer",
        "pain_point": "Photographers juggle client deposits, final payments, equipment purchases, and travel expenses — often mixing personal and business transactions.",
        "benefit": "Convert your bank statements to Excel to separate business income from personal spending, track equipment costs, and prepare your self-assessment return.",
        "keywords": ["photographer bank statement tool", "creative business accounting", "photographer expense tracker"],
        "combo_eligible": True,
    },
    "consultants": {
        "name": "Consultants",
        "singular": "Consultant",
        "pain_point": "Consultants managing multiple client engagements need to track retainers, milestone payments, travel expenses, and subcontractor costs across accounts.",
        "benefit": "Convert your bank statements to structured spreadsheets to match client payments, track project expenses, and prepare invoices and tax returns efficiently.",
        "keywords": ["consultant bank statement tool", "consulting practice accounting", "management consultant finance"],
        "combo_eligible": True,
    },
    "recruitment-agencies": {
        "name": "Recruitment Agencies",
        "singular": "Recruitment Agency Owner",
        "pain_point": "Recruitment agencies process candidate placements, client invoices, temporary worker payments, and margin calculations — requiring meticulous bank reconciliation.",
        "benefit": "Convert your agency bank statements to Excel to reconcile placement fees, match temporary worker payments, and track client account balances.",
        "keywords": ["recruitment agency bank statement", "staffing agency accounting", "placement fee reconciliation"],
        "combo_eligible": True,
    },
    "pubs-and-bars": {
        "name": "Pubs & Bars",
        "singular": "Pub Owner",
        "pain_point": "Pubs process high volumes of card and cash transactions, brewery payments, and entertainment costs — making daily bank reconciliation essential.",
        "benefit": "Convert your pub bank statements to Excel to reconcile daily takings, match brewery and supplier invoices, and prepare VAT returns accurately.",
        "keywords": ["pub bank statement tool", "bar accounting", "hospitality bank reconciliation"],
        "combo_eligible": True,
    },
    "hair-and-beauty": {
        "name": "Hair & Beauty Salons",
        "singular": "Salon Owner",
        "pain_point": "Hair and beauty salons handle a mix of card payments, cash transactions, product sales, and chair rental income — often without dedicated accounting staff.",
        "benefit": "Convert your salon bank statements to clean spreadsheets to track daily revenue, reconcile supplier payments, and prepare your books for your accountant.",
        "keywords": ["salon bank statement tool", "beauty salon accounting", "hairdresser bank reconciliation"],
        "combo_eligible": True,
    },
    "tradespeople": {
        "name": "Tradespeople (Plumbers, Electricians)",
        "singular": "Tradesperson",
        "pain_point": "Tradespeople receive payments from multiple customers, buy materials from suppliers, and manage van and tool expenses — often on the go with no time for bookkeeping.",
        "benefit": "Convert your bank statements to Excel to track job payments, match material purchases, and get your books ready for self-assessment without hours of data entry.",
        "keywords": ["tradesperson bank statement tool", "plumber accounting", "electrician bank statement converter"],
        "combo_eligible": True,
    },
    "care-homes": {
        "name": "Care Homes",
        "singular": "Care Home Manager",
        "pain_point": "Care homes receive local authority funding, private fees, and NHS contributions — each with different payment schedules and reconciliation requirements.",
        "benefit": "Convert your care home bank statements to Excel to reconcile council payments, private resident fees, and NHS funding allocations efficiently.",
        "keywords": ["care home bank statement tool", "care home accounting", "local authority payment reconciliation"],
        "combo_eligible": True,
    },
    "driving-instructors": {
        "name": "Driving Instructors",
        "singular": "Driving Instructor",
        "pain_point": "Driving instructors collect lesson fees via bank transfer, cash, and card — and need to track fuel, vehicle maintenance, and insurance for tax purposes.",
        "benefit": "Convert your bank statements to Excel to separate lesson income from vehicle expenses, making self-assessment tax returns straightforward.",
        "keywords": ["driving instructor bank statement", "ADI accounting tool", "driving school finance"],
        "combo_eligible": False,
    },
    "tutors": {
        "name": "Tutors & Educators",
        "singular": "Tutor",
        "pain_point": "Private tutors receiving payments from multiple students and platforms need to track income accurately for self-assessment and potential VAT registration.",
        "benefit": "Convert your bank statements to structured spreadsheets to identify all tutor income sources, track teaching expenses, and prepare your tax return.",
        "keywords": ["tutor bank statement tool", "private tutor accounting", "education freelancer finance"],
        "combo_eligible": False,
    },
    "cleaning-companies": {
        "name": "Cleaning Companies",
        "singular": "Cleaning Company Owner",
        "pain_point": "Cleaning companies manage recurring client payments, staff wages, supplies purchases, and vehicle costs — often across residential and commercial contracts.",
        "benefit": "Convert your bank statements to Excel to reconcile client payments against contracts, track staff costs, and manage supplier accounts efficiently.",
        "keywords": ["cleaning company bank statement", "cleaning business accounting", "janitorial service finance"],
        "combo_eligible": True,
    },
    "fitness-trainers": {
        "name": "Personal Trainers & Gyms",
        "singular": "Personal Trainer",
        "pain_point": "Personal trainers and gym owners handle membership payments, class fees, equipment purchases, and venue hire — often through multiple payment apps.",
        "benefit": "Convert your bank statements to structured spreadsheets to track membership income, match equipment purchases, and prepare your books for your accountant.",
        "keywords": ["personal trainer bank statement", "gym accounting tool", "fitness business finance"],
        "combo_eligible": True,
    },
}

# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------
FORMATS = {
    "excel": {"name": "Excel", "extension": ".xlsx", "icon": "table"},
    "csv": {"name": "CSV", "extension": ".csv", "icon": "file-text"},
    "google-sheets": {"name": "Google Sheets", "extension": "", "icon": "grid"},
    "ofx": {"name": "OFX", "extension": ".ofx", "icon": "file"},
    "qif": {"name": "QIF", "extension": ".qif", "icon": "file"},
    "qbo": {"name": "QBO", "extension": ".qbo", "icon": "file"},
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
    },
    "quickbooks": {
        "name": "QuickBooks",
        "full_name": "QuickBooks Online",
        "import_format": "CSV",
        "description": "QuickBooks Online is widely used by UK small businesses and their accountants for invoicing, expenses, and VAT.",
        "import_notes": "Export your converted bank statement as CSV and import directly into QuickBooks via Banking > Upload transactions. BankScan AI formats the columns correctly.",
    },
    "sage": {
        "name": "Sage",
        "full_name": "Sage Business Cloud Accounting",
        "import_format": "CSV",
        "description": "Sage is one of the UK's longest-established accounting software providers, used by thousands of SMEs.",
        "import_notes": "BankScan AI's CSV output is compatible with Sage's bank statement import. The date format, transaction descriptions, and amount columns are mapped automatically.",
    },
    "freeagent": {
        "name": "FreeAgent",
        "full_name": "FreeAgent",
        "import_format": "CSV / OFX",
        "description": "FreeAgent is popular with UK freelancers and sole traders, especially those with NatWest, RBS, or Mettle accounts.",
        "import_notes": "Convert your bank statement PDF with BankScan AI, then import the CSV into FreeAgent's bank statement upload. Dates and amounts are formatted to match FreeAgent's requirements.",
    },
    "kashflow": {
        "name": "KashFlow",
        "full_name": "KashFlow",
        "import_format": "CSV",
        "description": "KashFlow is a simple cloud accounting tool popular with UK small businesses who want straightforward bookkeeping.",
        "import_notes": "BankScan AI's CSV export works directly with KashFlow's bank statement import feature. Upload and match transactions in minutes.",
    },
    "wave": {
        "name": "Wave",
        "full_name": "Wave Accounting",
        "import_format": "CSV",
        "description": "Wave is a free accounting platform popular with freelancers and micro-businesses in the UK.",
        "import_notes": "Convert your bank statement to CSV with BankScan AI, then use Wave's import feature to upload transactions. The format is fully compatible.",
    },
    "zoho-books": {
        "name": "Zoho Books",
        "full_name": "Zoho Books",
        "import_format": "CSV",
        "description": "Zoho Books is a cloud accounting solution that integrates with the wider Zoho suite, used by growing UK businesses.",
        "import_notes": "BankScan AI's CSV output imports directly into Zoho Books' banking module. Transaction dates, descriptions, and amounts are correctly formatted.",
    },
    "freshbooks": {
        "name": "FreshBooks",
        "full_name": "FreshBooks",
        "import_format": "CSV / OFX",
        "description": "FreshBooks is popular with UK service businesses and freelancers for invoicing and expense tracking.",
        "import_notes": "Convert your bank statement PDF to CSV with BankScan AI, then import into FreshBooks to reconcile invoices and track expenses automatically.",
    },
    "clear-books": {
        "name": "Clear Books",
        "full_name": "Clear Books",
        "import_format": "CSV",
        "description": "Clear Books is a UK-built cloud accounting platform designed specifically for small businesses and their accountants.",
        "import_notes": "BankScan AI's CSV output is formatted for direct import into Clear Books' bank reconciliation feature.",
    },
    "iris": {
        "name": "IRIS",
        "full_name": "IRIS Accountancy Suite",
        "import_format": "CSV",
        "description": "IRIS is used by thousands of UK accountancy practices for compliance, tax, and bookkeeping.",
        "import_notes": "Convert client bank statements to CSV with BankScan AI, then import into IRIS for fast bank reconciliation across your client portfolio.",
    },
    "dext": {
        "name": "Dext",
        "full_name": "Dext (formerly Receipt Bank)",
        "import_format": "CSV",
        "description": "Dext automates data extraction from receipts and invoices for accountants and bookkeepers across the UK.",
        "import_notes": "BankScan AI complements Dext by converting bank statement PDFs to CSV. Import into Dext or directly into your accounting package for reconciliation.",
    },
    "taxcalc": {
        "name": "TaxCalc",
        "full_name": "TaxCalc",
        "import_format": "CSV",
        "description": "TaxCalc is a UK tax return and accounts production software used by accountants and individuals for self-assessment and company filings.",
        "import_notes": "Convert bank statements to CSV with BankScan AI and use the data to prepare self-assessment, partnership, and corporation tax returns in TaxCalc.",
    },
    "taxfiler": {
        "name": "Taxfiler",
        "full_name": "Taxfiler",
        "import_format": "CSV",
        "description": "Taxfiler is a cloud-based UK tax compliance platform for accountancy practices, covering personal tax, CT600, and accounts production.",
        "import_notes": "Convert client bank statements to structured CSV files with BankScan AI, then use the data for income verification and expense categorisation in Taxfiler.",
    },
    "pandle": {
        "name": "Pandle",
        "full_name": "Pandle",
        "import_format": "CSV / QIF",
        "description": "Pandle is a free bookkeeping software for UK sole traders and small businesses, with automatic bank feeds and simple invoicing.",
        "import_notes": "BankScan AI's CSV output imports directly into Pandle's bank statement upload feature. Perfect for sole traders who receive statements from clients or banks without direct feeds.",
    },
    "coconut": {
        "name": "Coconut",
        "full_name": "Coconut",
        "import_format": "CSV",
        "description": "Coconut is a UK business banking and accounting app for freelancers and sole traders that combines banking with bookkeeping.",
        "import_notes": "Convert statements from other bank accounts to CSV with BankScan AI and import them into Coconut to get a complete picture of your business finances.",
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
    },
    "visa-application": {
        "name": "Visa Application",
        "description": "Prepare bank statements for UK visa and immigration applications",
        "pain_point": "UK visa applications (Tier 1, Tier 2, spouse, student) require bank statements proving financial capability. The Home Office needs clear, organised financial evidence.",
        "benefit": "Convert your bank statements to structured Excel spreadsheets that clearly show your balance history, income sources, and regular savings — formatted for visa evidence bundles.",
        "keywords": "bank statement for visa, UK visa bank statement, immigration financial evidence, spouse visa bank statement",
    },
    "tax-return": {
        "name": "Self-Assessment Tax Return",
        "description": "Prepare bank statements for HMRC self-assessment tax returns",
        "pain_point": "Self-assessment requires reviewing a full year of bank transactions to identify income, allowable expenses, and taxable events. Doing this manually from PDFs takes hours.",
        "benefit": "Convert 12 months of bank statements to Excel in minutes. Filter, sort, and categorise transactions to identify income and allowable expenses for your SA100.",
        "keywords": "bank statement for tax return, self assessment bank statement, HMRC tax return statement converter",
    },
    "hmrc-investigation": {
        "name": "HMRC Investigation",
        "description": "Prepare bank statements for HMRC tax investigations and enquiries",
        "pain_point": "HMRC investigations can request years of bank statements. Converting these to analysable data is critical for responding quickly and accurately.",
        "benefit": "Batch-convert multiple years of bank statements to structured spreadsheets for HMRC enquiry responses, voluntary disclosures, and tax investigation defence.",
        "keywords": "HMRC investigation bank statement, tax enquiry statement converter, HMRC compliance statement tool",
    },
    "divorce-proceedings": {
        "name": "Divorce Proceedings",
        "description": "Convert bank statements for financial disclosure in divorce cases",
        "pain_point": "Divorce financial disclosure (Form E) requires detailed bank statement analysis. Solicitors and clients need to review months of transactions to identify assets and spending.",
        "benefit": "Convert bank statements to searchable Excel spreadsheets for Form E preparation, asset tracing, and financial disclosure in divorce proceedings.",
        "keywords": "bank statement for divorce, Form E bank statement, financial disclosure statement converter, matrimonial finance tool",
    },
    "probate": {
        "name": "Probate & Estate Administration",
        "description": "Convert bank statements for probate applications and estate administration",
        "pain_point": "Executors and solicitors handling probate need to review the deceased's bank statements to value the estate, identify debts, and distribute assets.",
        "benefit": "Convert the deceased's bank statements to Excel for fast estate valuation, identification of standing orders, direct debits, and outstanding payments.",
        "keywords": "bank statement for probate, estate administration statement tool, executor bank statement converter",
    },
    "audit-preparation": {
        "name": "Audit Preparation",
        "description": "Prepare bank statements for statutory and internal audits",
        "pain_point": "Auditors need to verify bank balances and test transactions against financial statements. Getting bank data into a workable format is the first bottleneck.",
        "benefit": "Convert bank statements to structured Excel files for audit testing, balance verification, and transaction sampling. Save hours of data preparation.",
        "keywords": "bank statement for audit, audit preparation statement tool, statutory audit bank statement converter",
    },
    "business-loan": {
        "name": "Business Loan Application",
        "description": "Prepare bank statements for business loan and finance applications",
        "pain_point": "Lenders and finance brokers require 6-12 months of business bank statements to assess cash flow and creditworthiness. Disorganised PDFs slow down applications.",
        "benefit": "Convert your business bank statements to clean spreadsheets showing cash flow, revenue patterns, and regular commitments — accelerating your loan application.",
        "keywords": "bank statement for business loan, finance application statement, cash flow statement converter",
    },
    "company-accounts": {
        "name": "Annual Company Accounts",
        "description": "Prepare bank statements for Companies House annual accounts filing",
        "pain_point": "Preparing annual accounts for Companies House requires reconciling a full year of bank transactions. Many small companies still rely on PDF statements from their bank.",
        "benefit": "Convert your full year of bank statements to Excel for fast reconciliation, trial balance preparation, and Companies House filing.",
        "keywords": "bank statement for company accounts, Companies House statement converter, annual accounts bank reconciliation",
    },
    "grant-application": {
        "name": "Grant Application",
        "description": "Prepare bank statements for grant funding applications",
        "pain_point": "Grant applications often require evidence of financial health, cash flow, and how previous funding was spent — typically shown through bank statements.",
        "benefit": "Convert your bank statements to clear spreadsheets that demonstrate financial health, spending patterns, and fund usage for grant applications.",
        "keywords": "bank statement for grant application, funding evidence statement tool, charity grant bank statement",
    },
    "rent-application": {
        "name": "Rental Application",
        "description": "Prepare bank statements for tenant referencing and rental applications",
        "pain_point": "Letting agents and referencing agencies require 3 months of bank statements to verify tenant income and affordability. Tenants need to provide clear, readable statements.",
        "benefit": "Convert your bank statements to Excel format for clean, professional-looking financial evidence that speeds up your rental application.",
        "keywords": "bank statement for renting, tenant referencing statement, rental application bank statement converter",
    },
    "expense-report": {
        "name": "Expense Reporting",
        "description": "Convert bank statements for business expense reports and reimbursement",
        "pain_point": "Employees and business owners need to extract business expenses from personal or corporate bank statements for reimbursement claims and expense reporting.",
        "benefit": "Convert your bank statement to Excel, then quickly filter and categorise business expenses for reimbursement claims, P11D reporting, or management accounts.",
        "keywords": "bank statement for expenses, expense report statement tool, P11D bank statement converter, business expense tracker",
    },
    "bank-reconciliation": {
        "name": "Bank Reconciliation",
        "description": "Convert bank statements for monthly bank reconciliation",
        "pain_point": "Monthly bank reconciliation requires matching every bank transaction against your accounting records. Working from PDF statements makes this slow and error-prone.",
        "benefit": "Convert bank statement PDFs to Excel for fast side-by-side reconciliation with your accounting records. Sort by date, filter by amount, and spot discrepancies instantly.",
        "keywords": "bank reconciliation tool, bank statement reconciliation, monthly reconciliation statement converter",
    },
    "management-accounts": {
        "name": "Management Accounts",
        "description": "Prepare bank statements for monthly management accounts and reporting",
        "pain_point": "Preparing monthly management accounts requires categorising all bank transactions by cost centre, project, or department. PDF statements make this analysis difficult.",
        "benefit": "Convert bank statements to Excel spreadsheets for fast categorisation, pivot table analysis, and management reporting. Save hours of manual data extraction.",
        "keywords": "bank statement for management accounts, management reporting statement tool, monthly accounts bank statement",
    },
    "vat-return": {
        "name": "VAT Return",
        "description": "Prepare bank statements for quarterly VAT return filing",
        "pain_point": "Quarterly VAT returns require matching bank transactions to sales and purchase invoices. Working from PDF statements adds unnecessary time to an already tight deadline.",
        "benefit": "Convert bank statements to Excel to quickly identify VAT-bearing transactions, match against invoices, and prepare your VAT return accurately.",
        "keywords": "bank statement for VAT return, VAT reconciliation statement tool, Making Tax Digital bank statement",
    },
    "cash-flow-forecast": {
        "name": "Cash Flow Forecasting",
        "description": "Convert bank statements for cash flow analysis and forecasting",
        "pain_point": "Cash flow forecasting requires analysing historical bank transactions to predict future income and expenditure patterns. PDF statements can't be analysed programmatically.",
        "benefit": "Convert months of bank statements to Excel and use pivot tables, charts, and formulas to build accurate cash flow forecasts from real transaction data.",
        "keywords": "bank statement for cash flow, cash flow forecast statement tool, cash flow analysis bank statement",
    },
    "insurance-claim": {
        "name": "Insurance Claim",
        "description": "Prepare bank statements for insurance claims and loss evidence",
        "pain_point": "Insurance claims for business interruption, theft, or fraud require bank statements as evidence of financial loss. Insurers need clear, organised financial documentation.",
        "benefit": "Convert bank statements to structured Excel spreadsheets showing income patterns, loss periods, and transaction evidence for insurance claim submissions.",
        "keywords": "bank statement for insurance claim, business interruption evidence, insurance loss statement tool",
    },
    "investor-reporting": {
        "name": "Investor Reporting",
        "description": "Prepare bank statements for investor updates and due diligence",
        "pain_point": "Investors and VCs request bank statements during due diligence and ongoing reporting. Presenting raw PDF statements looks unprofessional and slows the process.",
        "benefit": "Convert your bank statements to clean Excel spreadsheets for professional investor reporting, burn rate analysis, and due diligence document preparation.",
        "keywords": "bank statement for investors, due diligence statement tool, investor reporting bank statement converter",
    },
    "debt-management": {
        "name": "Debt Management",
        "description": "Convert bank statements for debt management plans and IVA applications",
        "pain_point": "Debt management advisors and IVA supervisors need detailed analysis of a debtor's bank statements to assess income, essential spending, and disposable income.",
        "benefit": "Convert bank statements to Excel to quickly categorise income and expenditure, calculate disposable income, and prepare debt management or IVA proposals.",
        "keywords": "bank statement for debt management, IVA bank statement, debt advisor statement tool, income expenditure analysis",
    },
    "anti-money-laundering": {
        "name": "Anti-Money Laundering (AML)",
        "description": "Convert bank statements for AML checks and suspicious activity reporting",
        "pain_point": "AML compliance officers need to review bank statements to identify suspicious transactions, unusual patterns, and politically exposed person (PEP) activity.",
        "benefit": "Convert bank statements to searchable, sortable spreadsheets for systematic AML review, transaction pattern analysis, and suspicious activity report preparation.",
        "keywords": "bank statement for AML, anti money laundering statement tool, suspicious activity report bank statement, KYC statement converter",
    },
}

# ---------------------------------------------------------------------------
# Page generators — each returns a dict of {slug: page_data}
# ---------------------------------------------------------------------------


def _generate_bank_pages():
    """Generate /tools/convert-{bank}-statement-to-excel pages."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        slug = f"convert-{bank_slug}-statement-to-excel"
        pages[slug] = {
            "type": "bank",
            "title": f"Convert {bank['name']} Bank Statement to Excel | Free AI Converter",
            "h1": f"Convert {bank['name']} Bank Statements to Excel Instantly",
            "meta_description": f"Upload your {bank['name']} bank statement PDF and get a clean Excel spreadsheet in seconds. AI-powered converter built for UK accountants and bookkeepers.",
            "keywords": f"convert {bank['name']} statement to Excel, {bank['name']} PDF to Excel, {bank['name']} statement converter, {bank['name']} bank statement parser",
            "bank": bank,
            "bank_slug": bank_slug,
            "intro": f"Need to convert a {bank['full_name']} bank statement to Excel? BankScan AI uses artificial intelligence to parse your {bank['name']} PDF statement and produce a clean, formatted spreadsheet — ready to import into Xero, QuickBooks, Sage, or any bookkeeping software.",
            "steps": [
                {"title": f"Upload your {bank['name']} statement", "desc": f"Drag and drop your {bank['name']} bank statement PDF. We support all {bank['name']} statement formats including {', '.join(bank['formats'])}."},
                {"title": "AI parses every transaction", "desc": f"Our AI engine reads your {bank['name']} statement and extracts dates, descriptions, amounts, and running balances with high accuracy."},
                {"title": "Download your Excel file", "desc": "Get a formatted .xlsx spreadsheet with all transactions neatly organised. Ready to import into your accounting software."},
            ],
            "detail_paragraph": bank["statement_notes"],
            "faqs": [
                {"q": f"What {bank['name']} statement formats does BankScan AI support?", "a": f"BankScan AI supports {bank['name']} statements in {', '.join(bank['formats'])} format. Whether your client has downloaded the statement from online banking or scanned a paper copy, our AI engine can handle it."},
                {"q": f"How accurate is the {bank['name']} statement conversion?", "a": f"BankScan AI achieves over 99% accuracy on {bank['name']} statements. Our AI is specifically trained on UK bank statement formats and understands {bank['name']}'s layout, date formats, and transaction descriptions."},
                {"q": f"Can I batch-convert multiple {bank['name']} statements at once?", "a": f"Yes. With a paid plan, you can upload multiple {bank['name']} statement PDFs and convert them all in one go. Each statement produces its own Excel file."},
                {"q": "Is my data secure?", "a": "Absolutely. Your bank statements are processed in memory and deleted immediately after conversion. We never store your financial data. BankScan AI is built by a UK company with data protection as a core principle."},
            ],
            "cta_text": f"Convert Your {bank['name']} Statement Now",
        }
    return pages


def _generate_profession_pages():
    """Generate /tools/bank-statement-converter-for-{profession} pages."""
    pages = {}
    for prof_slug, prof in PROFESSIONS.items():
        slug = f"bank-statement-converter-for-{prof_slug}"
        pages[slug] = {
            "type": "profession",
            "title": f"Best Bank Statement Converter for {prof['name']} | BankScan AI",
            "h1": f"Bank Statement Converter for {prof['name']}",
            "meta_description": f"AI-powered bank statement converter built for {prof['name'].lower()}. Upload any UK bank statement PDF, get a clean Excel spreadsheet in seconds. Try free.",
            "keywords": ", ".join(prof["keywords"]),
            "profession": prof,
            "profession_slug": prof_slug,
            "intro": prof["pain_point"],
            "solution": prof["benefit"],
            "steps": [
                {"title": "Upload any bank statement PDF", "desc": "Supports all major UK banks — HSBC, Barclays, Lloyds, NatWest, Monzo, Santander, Revolut, and more."},
                {"title": "AI extracts every transaction", "desc": "Our AI reads the PDF and pulls out dates, descriptions, amounts, and balances with over 99% accuracy."},
                {"title": "Download your spreadsheet", "desc": "Get a formatted Excel file ready to import into your accounting software or use for analysis."},
            ],
            "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
            "faqs": [
                {"q": f"Is BankScan AI suitable for {prof['name'].lower()}?", "a": f"Yes. BankScan AI is specifically designed for {prof['name'].lower()} who need to convert bank statement PDFs to Excel spreadsheets. {prof['benefit']}"},
                {"q": "Which UK banks are supported?", "a": "BankScan AI supports all major UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Starling, Revolut, Halifax, Nationwide, RBS, TSB, Tide, Metro Bank, and more."},
                {"q": "How much does it cost?", "a": "BankScan AI offers a free tier so you can try it with no commitment. Paid plans start from \u00a37.99/month for higher volumes. No long-term contracts."},
                {"q": "Can I convert receipts as well as bank statements?", "a": "Yes. BankScan AI also converts receipt photos and scans to structured spreadsheets — ideal for expense tracking and VAT reclaims."},
            ],
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
            "meta_description": f"Scan receipts and convert them to Excel spreadsheets instantly. AI-powered receipt scanner for {prof['name'].lower()}. Supports photos, scans, and PDFs.",
            "keywords": f"receipt scanner for {prof['name'].lower()}, receipt to Excel, receipt OCR, expense tracker {prof['name'].lower()}",
            "profession": prof,
            "profession_slug": prof_slug,
            "intro": f"Stop manually typing receipt data. BankScan AI uses artificial intelligence to read your receipts — whether they're photos from your phone, scanned images, or PDF attachments — and converts them to structured Excel spreadsheets.",
            "steps": [
                {"title": "Upload receipt photos or PDFs", "desc": "Take a photo of any receipt, or upload scanned images and PDFs. Supports JPG, PNG, and PDF formats."},
                {"title": "AI reads every line item", "desc": "Our AI extracts the merchant name, date, items, amounts, VAT, and total with high accuracy."},
                {"title": "Download your spreadsheet", "desc": "Get a formatted Excel file with all receipt data organised for easy expense tracking and VAT reclaims."},
            ],
            "faqs": [
                {"q": f"Can {prof['name'].lower()} use this for expense tracking?", "a": f"Absolutely. BankScan AI's receipt scanner is perfect for {prof['name'].lower()} who need to track expenses. Upload receipt photos, get organised spreadsheets you can use for bookkeeping or tax returns."},
                {"q": "Does it handle VAT receipts?", "a": "Yes. BankScan AI extracts VAT amounts from receipts, making it easy to prepare VAT returns and expense claims."},
                {"q": "Can I scan multiple receipts at once?", "a": "Yes. With a paid plan, you can upload multiple receipt photos in one batch and get a single consolidated spreadsheet with all items."},
                {"q": "What if the receipt photo is blurry?", "a": "BankScan AI uses advanced AI that handles imperfect images well. As long as the text is reasonably legible, our engine will extract the data accurately."},
            ],
            "cta_text": "Scan Your Receipts Free",
        }
    return pages


def _generate_bank_format_pages():
    """Generate /tools/{bank}-pdf-to-{format} pages for ALL banks."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        for fmt_slug, fmt in FORMATS.items():
            slug = f"{bank_slug}-pdf-to-{fmt_slug}"
            pages[slug] = {
                "type": "bank_format",
                "title": f"Convert {bank['name']} PDF to {fmt['name']} | AI-Powered Converter",
                "h1": f"{bank['name']} PDF Statement to {fmt['name']}",
                "meta_description": f"Convert {bank['name']} bank statement PDFs to {fmt['name']} format instantly. AI-powered tool built for UK accountants. Accurate, fast, secure.",
                "keywords": f"{bank['name']} PDF to {fmt['name']}, {bank['name']} statement to {fmt['name']}, convert {bank['name']} PDF {fmt_slug}",
                "bank": bank,
                "bank_slug": bank_slug,
                "format": fmt,
                "format_slug": fmt_slug,
                "intro": f"Convert your {bank['full_name']} bank statement from PDF to {fmt['name']} format. BankScan AI's artificial intelligence reads the PDF, extracts every transaction, and outputs a clean {fmt['name']} file — preserving dates, descriptions, amounts, and running balances.",
                "steps": [
                    {"title": f"Upload your {bank['name']} PDF", "desc": f"Drop your {bank['name']} bank statement PDF into BankScan AI."},
                    {"title": "AI extracts all transactions", "desc": f"Our engine parses the {bank['name']} statement layout and pulls out every transaction with over 99% accuracy."},
                    {"title": f"Download as {fmt['name']}", "desc": f"Get your data as a clean {fmt['name']} file, ready for import into your accounting or analysis tools."},
                ],
                "faqs": [
                    {"q": f"Can I convert {bank['name']} statements to {fmt['name']} for free?", "a": f"Yes. BankScan AI offers a free tier that lets you convert {bank['name']} statements to {fmt['name']}. Paid plans offer higher volumes and batch conversion."},
                    {"q": f"Will the {fmt['name']} file preserve all transaction details?", "a": f"Yes. The {fmt['name']} output includes the transaction date, description, debit/credit amounts, and running balance — everything from your original {bank['name']} statement."},
                ],
                "cta_text": f"Convert {bank['name']} to {fmt['name']} Free",
            }
    return pages


def _generate_software_pages():
    """Generate /tools/import-bank-statement-to-{software} pages."""
    pages = {}
    for sw_slug, sw in ACCOUNTING_SOFTWARE.items():
        slug = f"import-bank-statement-to-{sw_slug}"
        pages[slug] = {
            "type": "software",
            "title": f"Import Bank Statement to {sw['name']} | PDF to {sw['name']} Converter",
            "h1": f"Import Bank Statements into {sw['name']}",
            "meta_description": f"Convert bank statement PDFs to {sw['import_format']} format for direct import into {sw['name']}. AI-powered converter for UK accountants and bookkeepers.",
            "keywords": f"import bank statement to {sw['name']}, {sw['name']} bank statement import, {sw['name']} statement converter, PDF to {sw['name']}",
            "software": sw,
            "software_slug": sw_slug,
            "intro": f"{sw['description']} But importing bank statements from PDF into {sw['name']} requires converting them to {sw['import_format']} first — and that's where BankScan AI comes in.",
            "solution": sw["import_notes"],
            "steps": [
                {"title": "Upload your bank statement PDF", "desc": "Drag and drop any UK bank statement PDF into BankScan AI. Supports HSBC, Barclays, Lloyds, NatWest, Monzo, and 15+ more banks."},
                {"title": "AI converts to the right format", "desc": f"Our AI extracts every transaction and formats the output as {sw['import_format']} — ready for {sw['name']}'s import feature."},
                {"title": f"Import into {sw['name']}", "desc": f"Download the file and upload it directly into {sw['name']}. Transactions appear ready for reconciliation."},
            ],
            "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
            "faqs": [
                {"q": f"Does BankScan AI work with {sw['name']}?", "a": f"Yes. BankScan AI produces {sw['import_format']} files specifically formatted for import into {sw['name']}. The column headers, date format, and amount format all match what {sw['name']} expects."},
                {"q": "Which banks are supported?", "a": "BankScan AI supports all major UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Starling, Revolut, Halifax, Nationwide, RBS, TSB, Tide, Metro Bank, and more."},
                {"q": f"Can I batch-import multiple statements into {sw['name']}?", "a": f"Yes. Convert multiple bank statement PDFs with BankScan AI, then import them one by one or combine them into a single file for {sw['name']}."},
                {"q": "Is the free tier enough for occasional use?", "a": "Yes. The free tier lets you convert a limited number of statements per month — ideal for trying BankScan AI before committing to a paid plan."},
            ],
            "cta_text": f"Convert for {sw['name']} — Free",
        }
    return pages


def _generate_use_case_pages():
    """Generate /tools/bank-statement-for-{use-case} pages."""
    pages = {}
    for uc_slug, uc in USE_CASES.items():
        slug = f"bank-statement-for-{uc_slug}"
        pages[slug] = {
            "type": "use_case",
            "title": f"Bank Statement Converter for {uc['name']} | BankScan AI",
            "h1": f"Convert Bank Statements for {uc['name']}",
            "meta_description": f"{uc['description']}. AI-powered converter supports all UK banks. Upload PDF, get Excel in seconds.",
            "keywords": uc["keywords"],
            "use_case": uc,
            "use_case_slug": uc_slug,
            "intro": uc["pain_point"],
            "solution": uc["benefit"],
            "steps": [
                {"title": "Upload your bank statement PDF", "desc": "Supports all major UK banks — HSBC, Barclays, Lloyds, NatWest, Monzo, Santander, Revolut, and more."},
                {"title": "AI extracts every transaction", "desc": "Our AI reads the statement and pulls out dates, descriptions, amounts, and balances with over 99% accuracy."},
                {"title": "Download and use", "desc": f"Get a clean Excel or CSV file formatted for {uc['name'].lower()}. Ready to submit, analyse, or share."},
            ],
            "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
            "faqs": [
                {"q": f"Can I use BankScan AI to prepare bank statements for {uc['name'].lower()}?", "a": f"Yes. BankScan AI converts bank statement PDFs to clean, structured Excel spreadsheets — ideal for {uc['name'].lower()}. {uc['benefit']}"},
                {"q": "Which UK banks are supported?", "a": "BankScan AI supports all major UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Starling, Revolut, Halifax, Nationwide, RBS, TSB, and more."},
                {"q": "Is my financial data secure?", "a": "Absolutely. Your bank statements are processed in memory and deleted immediately after conversion. We never store your financial data. BankScan AI is built by a UK company with GDPR compliance as standard."},
                {"q": "How quickly can I get my converted statements?", "a": "Most statements are converted in under 30 seconds. You can upload, convert, and download your Excel file in under a minute."},
            ],
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
            pages[slug] = {
                "type": "bank_profession",
                "title": f"Convert {bank['name']} Statement to Excel for {prof['name']} | BankScan AI",
                "h1": f"{bank['name']} Statement Converter for {prof['name']}",
                "meta_description": f"AI-powered {bank['name']} bank statement converter for {prof['name'].lower()}. Upload {bank['name']} PDFs, get Excel spreadsheets in seconds. Try free.",
                "keywords": f"{bank['name']} statement converter for {prof['name'].lower()}, {bank['name']} PDF to Excel {prof['name'].lower()}, {prof['name'].lower()} {bank['name']} tool",
                "bank": bank,
                "bank_slug": bank_slug,
                "profession": prof,
                "profession_slug": prof_slug,
                "intro": f"As {prof['singular'].lower() if prof['singular'][0].lower() not in 'aeiou' else 'an ' + prof['singular'].lower()}, you regularly handle {bank['name']} bank statements. {prof['pain_point']}",
                "solution": f"BankScan AI converts {bank['name']} statements to Excel automatically. {prof['benefit']}",
                "detail_paragraph": bank["statement_notes"],
                "steps": [
                    {"title": f"Upload {bank['name']} statement", "desc": f"Drag and drop your client's or your own {bank['name']} bank statement PDF. Supports {', '.join(bank['formats'])} formats."},
                    {"title": "AI parses the statement", "desc": f"Our AI understands {bank['name']}'s specific statement layout and extracts every transaction with over 99% accuracy."},
                    {"title": "Download your Excel file", "desc": f"Get a formatted spreadsheet ready for your {prof['name'].lower()} workflow — import into accounting software or use for analysis."},
                ],
                "faqs": [
                    {"q": f"Is BankScan AI good for {prof['name'].lower()} handling {bank['name']} statements?", "a": f"Yes. BankScan AI is specifically trained on {bank['name']}'s statement format and designed for {prof['name'].lower()}. {prof['benefit']}"},
                    {"q": f"What {bank['name']} formats are supported?", "a": f"BankScan AI supports {bank['name']} statements in {', '.join(bank['formats'])} format — whether downloaded from online banking or scanned from paper."},
                    {"q": "Can I convert statements from other banks too?", "a": "Yes. BankScan AI supports 15+ UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Revolut, and more."},
                    {"q": "Is there a free option?", "a": "Yes. BankScan AI offers a free tier so you can try the converter with no commitment. Paid plans from \u00a37.99/month unlock batch conversion and higher volumes."},
                ],
                "cta_text": f"Convert {bank['name']} Statements Free",
            }
    return pages


def _generate_bank_software_pages():
    """Generate /tools/import-{bank}-to-{software} pages."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        for sw_slug, sw in ACCOUNTING_SOFTWARE.items():
            slug = f"import-{bank_slug}-to-{sw_slug}"
            pages[slug] = {
                "type": "bank_software",
                "title": f"Import {bank['name']} Statement to {sw['name']} | AI Converter",
                "h1": f"Import {bank['name']} Bank Statement into {sw['name']}",
                "meta_description": f"Convert {bank['name']} bank statement PDFs to {sw['import_format']} and import directly into {sw['name']}. AI-powered, accurate, and fast.",
                "keywords": f"import {bank['name']} to {sw['name']}, {bank['name']} statement {sw['name']}, {bank['name']} PDF to {sw['name']}, {bank['name']} {sw['name']} import",
                "bank": bank,
                "bank_slug": bank_slug,
                "software": sw,
                "software_slug": sw_slug,
                "intro": f"Need to get your {bank['full_name']} bank statement into {sw['name']}? {bank['name']} doesn't always make it easy to export transactions in a format {sw['name']} accepts. BankScan AI bridges the gap — upload your {bank['name']} PDF, get a {sw['import_format']} file ready for {sw['name']} import.",
                "solution": f"{sw['import_notes']} {bank['statement_notes']}",
                "steps": [
                    {"title": f"Upload your {bank['name']} PDF", "desc": f"Drag and drop your {bank['name']} bank statement into BankScan AI. Supports {', '.join(bank['formats'])} formats."},
                    {"title": f"AI converts for {sw['name']}", "desc": f"Our AI parses the {bank['name']} statement layout and outputs a {sw['import_format']} file formatted for {sw['name']}'s import feature."},
                    {"title": f"Import into {sw['name']}", "desc": f"Upload the converted file into {sw['name']} and start reconciling transactions immediately."},
                ],
                "faqs": [
                    {"q": f"Can I import {bank['name']} statements directly into {sw['name']}?", "a": f"{bank['name']} PDF statements can't be imported directly into {sw['name']}. BankScan AI converts them to {sw['import_format']} format that {sw['name']} accepts, preserving all transaction details."},
                    {"q": f"Does the converted file match {sw['name']}'s import format?", "a": f"Yes. BankScan AI formats the {sw['import_format']} output with the correct column headers, date format, and amount structure that {sw['name']} expects for bank statement import."},
                    {"q": f"How accurate is the {bank['name']} to {sw['name']} conversion?", "a": f"BankScan AI achieves over 99% accuracy on {bank['name']} statements. Our AI is trained specifically on UK bank statement formats."},
                    {"q": "Is there a free option?", "a": "Yes. BankScan AI offers a free tier to try the converter. Paid plans from \u00a37.99/month for higher volumes."},
                ],
                "cta_text": f"Import {bank['name']} to {sw['name']} Free",
            }
    return pages


def _generate_bank_usecase_pages():
    """Generate /tools/{bank}-statement-for-{use-case} pages."""
    pages = {}
    for bank_slug, bank in BANKS.items():
        for uc_slug, uc in USE_CASES.items():
            slug = f"{bank_slug}-statement-for-{uc_slug}"
            pages[slug] = {
                "type": "bank_usecase",
                "title": f"Convert {bank['name']} Statement for {uc['name']} | BankScan AI",
                "h1": f"{bank['name']} Statement Converter for {uc['name']}",
                "meta_description": f"Convert your {bank['name']} bank statement to Excel for {uc['name'].lower()}. AI-powered, accurate, instant. Supports {', '.join(bank['formats'])} formats.",
                "keywords": f"{bank['name']} statement for {uc['name'].lower()}, convert {bank['name']} PDF {uc['name'].lower()}, {bank['name']} bank statement {uc_slug}",
                "bank": bank,
                "bank_slug": bank_slug,
                "use_case": uc,
                "use_case_slug": uc_slug,
                "intro": f"Preparing {bank['name']} bank statements for {uc['name'].lower()}? {uc['pain_point']}",
                "solution": f"BankScan AI converts your {bank['name']} statement PDF to a clean Excel spreadsheet in seconds. {uc['benefit']}",
                "detail_paragraph": bank["statement_notes"],
                "steps": [
                    {"title": f"Upload your {bank['name']} statement", "desc": f"Drop your {bank['full_name']} bank statement PDF into BankScan AI. Supports {', '.join(bank['formats'])}."},
                    {"title": "AI extracts every transaction", "desc": f"Our AI understands {bank['name']}'s statement layout and pulls out dates, descriptions, amounts, and balances."},
                    {"title": f"Use for {uc['name'].lower()}", "desc": f"Download a formatted Excel file ready for {uc['name'].lower()}. Sort, filter, and analyse as needed."},
                ],
                "faqs": [
                    {"q": f"Can I use a {bank['name']} statement for {uc['name'].lower()}?", "a": f"Yes. BankScan AI converts {bank['name']} bank statement PDFs to structured Excel spreadsheets that are ideal for {uc['name'].lower()}. {uc['benefit']}"},
                    {"q": f"What {bank['name']} statement formats work?", "a": f"BankScan AI supports {bank['name']} statements in {', '.join(bank['formats'])} format — whether downloaded from online banking or scanned from a paper statement."},
                    {"q": "How quickly is the statement converted?", "a": f"Most {bank['name']} statements are converted in under 30 seconds. Upload, convert, and download in under a minute."},
                    {"q": "Is my data secure?", "a": "Your bank statements are processed in memory and deleted immediately. We never store your financial data."},
                ],
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
            pages[slug] = {
                "type": "software_profession",
                "title": f"Import Bank Statements to {sw['name']} for {prof['name']} | BankScan AI",
                "h1": f"{sw['name']} Bank Statement Import for {prof['name']}",
                "meta_description": f"Convert bank statement PDFs to {sw['import_format']} for {sw['name']} import. Built for {prof['name'].lower()}. Supports all UK banks. Try free.",
                "keywords": f"{sw['name']} for {prof['name'].lower()}, {sw['name']} bank import {prof['name'].lower()}, {prof['name'].lower()} {sw['name']} statement tool",
                "software": sw,
                "software_slug": sw_slug,
                "profession": prof,
                "profession_slug": prof_slug,
                "intro": f"{prof['pain_point']} If you use {sw['name']} for your accounting, you need a fast way to get bank statement data into the system.",
                "solution": f"BankScan AI converts any UK bank statement PDF to {sw['import_format']} formatted for direct import into {sw['name']}. {prof['benefit']}",
                "steps": [
                    {"title": "Upload any bank statement PDF", "desc": "Supports all major UK banks — HSBC, Barclays, Lloyds, NatWest, Monzo, Santander, Revolut, and 15+ more."},
                    {"title": f"AI formats for {sw['name']}", "desc": f"Our AI extracts every transaction and outputs a {sw['import_format']} file with the exact column format {sw['name']} expects."},
                    {"title": f"Import into {sw['name']}", "desc": f"Upload the file into {sw['name']} and reconcile transactions. No manual data entry needed."},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Is BankScan AI good for {prof['name'].lower()} using {sw['name']}?", "a": f"Yes. BankScan AI is designed for {prof['name'].lower()} who need to import bank statements into {sw['name']}. {prof['benefit']}"},
                    {"q": f"Does the output work with {sw['name']}?", "a": f"Yes. BankScan AI produces {sw['import_format']} files formatted specifically for {sw['name']}'s bank statement import feature."},
                    {"q": "Which banks are supported?", "a": "All major UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Revolut, and 20+ more."},
                    {"q": "Is there a free tier?", "a": "Yes. Try BankScan AI free with limited conversions per month. Paid plans from \u00a37.99/month."},
                ],
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
            pages[slug] = {
                "type": "profession_usecase",
                "title": f"Bank Statement Converter for {prof['name']} — {uc['name']} | BankScan AI",
                "h1": f"Bank Statement Converter for {prof['name']}: {uc['name']}",
                "meta_description": f"Convert bank statements for {uc['name'].lower()} as {prof['singular'].lower() if not prof['singular'][0].lower() in 'aeiou' else 'an ' + prof['singular'].lower()}. AI-powered, all UK banks, instant results.",
                "keywords": f"{prof['name'].lower()} bank statement {uc['name'].lower()}, {prof['name'].lower()} {uc_slug} statement tool, {uc['name'].lower()} for {prof['name'].lower()}",
                "profession": prof,
                "profession_slug": prof_slug,
                "use_case": uc,
                "use_case_slug": uc_slug,
                "intro": f"As {prof['singular'].lower() if not prof['singular'][0].lower() in 'aeiou' else 'an ' + prof['singular'].lower()}, preparing bank statements for {uc['name'].lower()} comes with specific challenges. {uc['pain_point']}",
                "solution": f"BankScan AI converts any UK bank statement PDF to a structured Excel spreadsheet in seconds. {uc['benefit']} {prof['benefit']}",
                "steps": [
                    {"title": "Upload any bank statement PDF", "desc": "Supports all major UK banks — HSBC, Barclays, Lloyds, NatWest, Monzo, Santander, Revolut, and 30+ more."},
                    {"title": "AI extracts every transaction", "desc": "Our AI pulls out dates, descriptions, amounts, and balances with over 99% accuracy from any bank's format."},
                    {"title": f"Use for {uc['name'].lower()}", "desc": f"Download a formatted Excel file ready for {uc['name'].lower()}. Sort, filter, and analyse as needed for your {prof['name'].lower()} workflow."},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Can {prof['name'].lower()} use BankScan AI for {uc['name'].lower()}?", "a": f"Yes. BankScan AI is ideal for {prof['name'].lower()} who need to convert bank statements for {uc['name'].lower()}. {uc['benefit']}"},
                    {"q": "Which banks are supported?", "a": "All major UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Revolut, and 30+ more."},
                    {"q": "How fast is the conversion?", "a": "Most statements are converted in under 30 seconds. Upload, convert, and download in under a minute."},
                    {"q": "Is there a free option?", "a": "Yes. Try BankScan AI free with limited conversions. Paid plans from \u00a37.99/month."},
                ],
                "cta_text": f"Convert for {uc['name']} — Free",
            }
    return pages


def _generate_software_usecase_pages():
    """Generate /tools/{software}-import-for-{use-case} pages."""
    pages = {}
    for sw_slug, sw in ACCOUNTING_SOFTWARE.items():
        for uc_slug, uc in USE_CASES.items():
            slug = f"{sw_slug}-import-for-{uc_slug}"
            pages[slug] = {
                "type": "software_usecase",
                "title": f"Import Bank Statement to {sw['name']} for {uc['name']} | BankScan AI",
                "h1": f"Import Bank Statements into {sw['name']} for {uc['name']}",
                "meta_description": f"Convert bank statement PDFs for {uc['name'].lower()} and import into {sw['name']}. AI-powered converter for UK accountants. All banks supported.",
                "keywords": f"{sw['name']} {uc['name'].lower()}, import bank statement {sw['name']} {uc['name'].lower()}, {uc_slug} {sw['name']} tool",
                "software": sw,
                "software_slug": sw_slug,
                "use_case": uc,
                "use_case_slug": uc_slug,
                "intro": f"Preparing for {uc['name'].lower()} and using {sw['name']}? {uc['pain_point']} BankScan AI bridges the gap between your bank's PDF statements and {sw['name']}'s import feature.",
                "solution": f"Convert bank statement PDFs to {sw['import_format']} formatted for {sw['name']} import. {uc['benefit']}",
                "steps": [
                    {"title": "Upload your bank statement PDF", "desc": "Supports all major UK banks — HSBC, Barclays, Lloyds, NatWest, Monzo, Santander, Revolut, and 30+ more."},
                    {"title": f"AI formats for {sw['name']}", "desc": f"Our AI extracts every transaction and outputs {sw['import_format']} with the exact column format {sw['name']} expects."},
                    {"title": f"Import and use for {uc['name'].lower()}", "desc": f"Upload into {sw['name']} and use the reconciled data for {uc['name'].lower()}."},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Can I import bank statements into {sw['name']} for {uc['name'].lower()}?", "a": f"Yes. BankScan AI converts bank statement PDFs to {sw['import_format']} formatted for {sw['name']}. {uc['benefit']}"},
                    {"q": f"Does the output match {sw['name']}'s import format?", "a": f"Yes. The {sw['import_format']} output uses the exact column headers, date format, and amount structure that {sw['name']} expects."},
                    {"q": "Which banks are supported?", "a": "All major UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Revolut, and 30+ more."},
                    {"q": "Is there a free tier?", "a": "Yes. Try free with limited conversions. Paid plans from \u00a37.99/month."},
                ],
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
                    "meta_description": f"Convert {bank['name']} bank statement PDFs to {fmt['name']} for {prof['name'].lower()}. AI-powered, accurate, fast. Built for UK professionals.",
                    "keywords": f"{bank['name']} to {fmt['name']} for {prof['name'].lower()}, {bank['name']} {fmt_slug} converter {prof_slug}, {bank_slug} statement {fmt_slug} {prof_slug}",
                    "bank": bank,
                    "bank_slug": bank_slug,
                    "profession": prof,
                    "profession_slug": prof_slug,
                    "format": fmt,
                    "format_slug": fmt_slug,
                    "intro": f"As a {prof['name'].lower()}, you need {bank['name']} bank statements in {fmt['name']} format. {bank.get('statement_notes', '')} BankScan AI handles the conversion automatically — accurate, fast, and secure.",
                    "steps": [
                        {"title": f"Upload {bank['name']} PDF", "desc": f"Drop your {bank['name']} bank statement PDF into BankScan AI. Scanned and digital PDFs both supported."},
                        {"title": f"AI converts to {fmt['name']}", "desc": f"Our engine parses the {bank['name']} layout and extracts every transaction with 99%+ accuracy into {fmt['name']} format."},
                        {"title": f"Use in your {prof['name'].lower()} workflow", "desc": f"Download the {fmt['name']} file and import it into your tools. {prof.get('benefit', 'Perfect for professional use.')}"},
                    ],
                    "faqs": [
                        {"q": f"Can I convert {bank['name']} statements to {fmt['name']}?", "a": f"Yes. BankScan AI converts {bank['name']} bank statement PDFs to {fmt['name']} with full transaction details preserved — dates, descriptions, amounts, and balances."},
                        {"q": f"Is this tool suitable for {prof['name'].lower()}?", "a": f"Absolutely. BankScan AI is built for UK professionals including {prof['name'].lower()}. The {fmt['name']} output is ready for your accounting and analysis workflows."},
                        {"q": "How accurate is the conversion?", "a": "BankScan AI achieves over 99% accuracy on supported bank statements, including complex multi-page PDFs and scanned documents."},
                    ],
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
                "meta_description": f"Convert any UK bank statement PDF to {fmt['name']} for {prof['name'].lower()}. AI-powered converter supporting HSBC, Barclays, Lloyds, NatWest, and 30+ banks.",
                "keywords": f"bank statement to {fmt['name']} for {prof['name'].lower()}, {fmt_slug} converter {prof_slug}, {prof_slug} bank statement {fmt_slug}",
                "profession": prof,
                "profession_slug": prof_slug,
                "format": fmt,
                "format_slug": fmt_slug,
                "intro": f"As a {prof['name'].lower()}, converting bank statements to {fmt['name']} saves hours of manual data entry. BankScan AI converts any UK bank statement PDF to a clean {fmt['name']} file — preserving every transaction detail.",
                "steps": [
                    {"title": "Upload any bank statement PDF", "desc": "Supports all major UK banks — HSBC, Barclays, Lloyds, NatWest, Monzo, Santander, Revolut, and 30+ more."},
                    {"title": f"AI outputs {fmt['name']}", "desc": f"Our AI reads the PDF layout, extracts all transactions, and outputs a clean {fmt['name']} file with dates, descriptions, amounts, and balances."},
                    {"title": f"Use in your {prof['name'].lower()} work", "desc": f"Download and import the {fmt['name']} file into your workflow. {prof.get('benefit', 'Perfect for professional use.')}"},
                ],
                "banks_supported": [b["name"] for b in BANKS.values() if b["popular"]],
                "faqs": [
                    {"q": f"Can I convert bank statements to {fmt['name']}?", "a": f"Yes. BankScan AI converts any UK bank statement PDF to {fmt['name']} format with 99%+ accuracy."},
                    {"q": f"Is {fmt['name']} the right format for {prof['name'].lower()}?", "a": f"{fmt['name']} is widely used by {prof['name'].lower()} for importing data into accounting software, spreadsheets, and analysis tools."},
                    {"q": "Which banks are supported?", "a": "All major UK banks including HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Revolut, Starling, Halifax, Nationwide, and 20+ more."},
                ],
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
