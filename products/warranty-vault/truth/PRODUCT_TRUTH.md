---
product_id: warranty-vault
product_name: Warranty Vault
package_id: com.warrantyvault.android
current_version: "1.0.5"
version_code: 5
platform: Android

description: >-
  Warranty Vault (branded in-app as "Vexel Warranty Vault") is a free,
  offline-first Android app for keeping claim-ready purchase records: product
  details, warranty coverage, return/replacement deadlines, optional
  insurance information, and user-selected receipts or supporting documents,
  all stored locally on the device without an account.

approved_features:
  - "Lets users add a product record with product name, category, and purchase date"
  - "Lets users optionally record brand, purchase location, serial number, seller contact, and notes on a product record"
  - "Lets users record a main warranty with a required duration in days, months, or years, plus optional provider, provider contact, coverage notes, start date, and expiry date"
  - "Lets users enable a component warranty on a product record"
  - "Lets users track return and replacement deadlines with period, deadline, address, contact, and notes"
  - "Lets users mark a product as insured"
  - "Lets users upload a receipt as proof of purchase"
  - "Lets users upload a warranty card and an instruction booklet as supporting documents"
  - "Shows dashboard totals for total products, items expiring soon, return deadlines, and active warranties"
  - "Shows a list of recent products with an active/expiring status badge on the Home screen"
  - "Lets users browse all saved products in a Product Inventory list with a grid/card view toggle and search"
  - "Shows an Expiring Soon screen that lists upcoming warranty and return deadlines"
  - "Lets users enable local notification reminders for expiry dates from Settings"
  - "Works fully offline with all records stored locally on the device"
  - "Does not require an account to use"

prohibited_claims:
  - "Guarantees warranty coverage, claim approval, reimbursement, or retailer acceptance"
  - "Automatically photographs, scans, or OCRs receipts or documents"
  - "Imports warranties or receipts from email automatically"
  - "Provides cloud backup, cloud sync, or cross-device synchronization"
  - "Requires or supports accounts, login, or family sharing"
  - "Supports barcode scanning or retailer/manufacturer integrations"
  - "Automatically registers warranties, files claims, or contacts retailers/manufacturers"
  - "Automatically verifies that a warranty is valid or that a receipt will be accepted as proof of purchase"
  - "Provides AI categorization, AI suggestions, service logs, advanced analytics, or home-map browsing"
  - "Ensures data can never be lost, or that a device loss cannot result in lost records"
  - "Is 100% secure, unhackable, or uses 'military-grade' security"
  - "Is completely ad-free, or claims zero data collection, when the release build includes advertising and consent SDKs"
  - "Works with every retailer or manufacturer"
  - "Is an ERP, accounting system, insurance service, or automated claims-processing platform"

known_limitations:
  - "iOS is not available; the active release target is Android only"
  - "Records and attachments are local to the device and are not cloud-synced or backed up automatically"
  - "Attachments are user-selected files; the app does not extract data from them with OCR"
  - "No in-app backup/restore flow was observed during the verification walkthrough; do not advertise backup/restore"
  - "Advertising availability (if any) depends on network state, consent status, SDK initialization, and ad inventory, and was not observed during an offline emulator walkthrough"
  - "A lost or replaced device without a manual backup may result in loss of locally stored records"

privacy_claims:
  - "Product, warranty, return, insurance, and attachment records are stored locally on the device"
  - "No account or login is required to use the app"
  - "The Settings screen states records are 'Stored locally on this device' and 'No account or cloud sync required'"
  - "The app requests notification permission to deliver local expiry reminders"

audiences:
  - "People who want a personal offline warranty and receipt organizer"
  - "Households tracking appliances, electronics, furniture, and other purchased products"
  - "People who frequently misplace receipts or cannot remember where proof of purchase is stored"

demo_workflows:
  - name: "First launch onboarding (may not always appear)"
    steps: >-
      On some fresh installs, the app opens to a 'Welcome to Vexel Warranty
      Vault' onboarding screen with 'Skip' and 'Get started' buttons below
      the fold (scroll down to reveal them). This screen's appearance is not
      guaranteed on every fresh install/data-clear cycle -- any capture
      sequence must treat dismissing it as optional (tap only if present),
      not assume it will or won't show.
  - name: "Add a product with a warranty"
    steps: >-
      From the Home screen tap 'Add product' (or 'Add your first product' on
      the empty state), which opens 'New Product'. On the 'Product' tab enter
      Product Name and select a Category from the exact taxonomy: 'Computer
      or Laptop', 'Electronics', 'Furniture', 'Home Appliance', 'Kitchen
      Appliance', 'Mobile Phone', 'Other', 'Personal Care', 'Tools' -- there
      is no generic 'Appliances' option, use 'Home Appliance' or 'Kitchen
      Appliance' instead. Purchase Date is required and defaults to today.
      Switch to the 'Coverage' tab and enter a Warranty Duration value with
      unit Days/Months/Years (required to save). Tap 'Save Product'.
  - name: "Attach a receipt to a product"
    steps: >-
      Open a saved product from Home or the Products tab, tap the edit
      (pencil-grid) icon to reopen 'Edit Product', switch to the 'Documents'
      tab, and tap 'Add receipt' under 'Receipt'.
  - name: "Check dashboard status"
    steps: >-
      Open the Home screen to see the 'Products', 'Expiring Soon', 'Return
      Deadlines', and 'Active warranties' count tiles, and a 'Recent
      products' list with an ACTIVE status badge per product.
  - name: "Check the Expiring Soon view"
    steps: >-
      Tap 'Expiring' in the bottom navigation to open the 'Expiring Soon'
      screen, which lists products with approaching warranty or return
      deadlines (shows 'You're all clear' when none are approaching).

evidence:
  - claim: "Lets users add a product record with product name, category, and purchase date"
    status: CURRENT
    source: "Direct UI walkthrough on production APK v1.0.5 (versionCode 5), AdForge Android emulator, 2026-08-29: New Product > Product tab shows required fields Product Name*, Category*, Purchase Date*."
  - claim: "Lets users optionally record brand, purchase location, serial number, seller contact, and notes on a product record"
    status: CURRENT
    source: "Direct UI walkthrough, New Product > Product tab: Brand, Seller Contact, Serial Number, Location, Notes fields observed, all unmarked (optional)."
  - claim: "Lets users record a main warranty with a required duration in days, months, or years, plus optional provider, provider contact, coverage notes, start date, and expiry date"
    status: CURRENT
    source: "Direct UI walkthrough, New Product > Coverage tab: Main Warranty section with Provider, Provider Contact, Coverage Notes, Start Date, Expiry Date fields, and a required 'Warranty Duration *' field with DAYS/MONTHS/YEARS selector; attempting Save without it produced the in-app error 'Warranty duration is required.'"
  - claim: "Lets users enable a component warranty on a product record"
    status: CURRENT
    source: "Direct UI walkthrough, New Product > Coverage tab: a 'Component warranty' toggle is present below the Main Warranty section."
  - claim: "Lets users track return and replacement deadlines with period, deadline, address, contact, and notes"
    status: CURRENT
    source: "Direct UI walkthrough, New Product > Coverage tab > Return Tracking section: Return Period (days), Return Deadline, Replacement Period (days), Replacement Deadline, Return Address, Return Contact, Notes fields observed."
  - claim: "Lets users mark a product as insured"
    status: CURRENT
    source: "Direct UI walkthrough, New Product > Coverage tab > Insurance section: an 'Insured' toggle observed; saved product's detail view showed 'Return & Insurance: Insured No'."
  - claim: "Lets users upload a receipt as proof of purchase"
    status: CURRENT
    source: "Direct UI walkthrough, New Product > Documents tab > Receipt section: 'Add proof of purchase' with an 'Add receipt' button observed."
  - claim: "Lets users upload a warranty card and an instruction booklet as supporting documents"
    status: CURRENT
    source: "Direct UI walkthrough, New Product > Documents tab > Other documents section: 'Warranty Card' and 'Instruction Booklet' rows each with an 'Upload' button, plus a 'Component warranty document' toggle, observed."
  - claim: "Shows dashboard totals for total products, items expiring soon, return deadlines, and active warranties"
    status: CURRENT
    source: "Direct UI walkthrough, Home screen after saving one product: four tiles labeled 'Products' (1), 'Expiring Soon' (0), 'Return Deadlines' (0), 'Active warranties' (1) observed."
  - claim: "Shows a list of recent products with an active/expiring status badge on the Home screen"
    status: CURRENT
    source: "Direct UI walkthrough, Home screen 'Recent products' section: product card with an 'ACTIVE' badge, category, and purchase date observed."
  - claim: "Lets users browse all saved products in a Product Inventory list with a grid/card view toggle and search"
    status: CURRENT
    source: "Direct UI walkthrough, Products tab: header 'Product Inventory' with a search icon and a grid-view toggle icon observed."
  - claim: "Shows an Expiring Soon screen that lists upcoming warranty and return deadlines"
    status: CURRENT
    source: "Direct UI walkthrough, Expiring tab: header 'Expiring Soon', empty state 'You're all clear / No warranties or return deadlines are approaching right now.' observed."
  - claim: "Lets users enable local notification reminders for expiry dates from Settings"
    status: CURRENT
    source: "Direct UI walkthrough, Settings tab > Reminders section: 'Local device reminders for expiry dates', 'Notifications off', and an 'Enable notifications' button observed."
  - claim: "Works fully offline with all records stored locally on the device"
    status: CURRENT
    source: "Direct UI walkthrough, Settings tab > Privacy section: 'Stored locally on this device / No account or cloud sync required.' text observed; Home screen shows the same tagline with a lock icon."
  - claim: "Does not require an account to use"
    status: CURRENT
    source: "Direct UI walkthrough, onboarding screen: 'Before you start / No account is required. You can skip this screen and start using the app immediately.' observed; app was used end-to-end with no login prompt."
  - claim: "The Android application package and current release version are com.warrantyvault.android, version 1.0.5, version code 5"
    status: CURRENT
    source: "aapt dump badging on products/warranty-vault/apk/android-app-release.apk, 2026-08-29, confirmed identically by the in-app Settings screen ('App version: 1.0.5 (5)')."
  - claim: "The Settings screen states records are 'Stored locally on this device' and 'No account or cloud sync required'"
    status: CURRENT
    source: "Direct UI walkthrough, Settings tab > Privacy section, verbatim text observed."

verification_rules:
  - "Production APK behavior observed via direct emulator walkthrough is the highest-priority evidence source, above prior documentation or source-derived truthmaps."
  - "Any UI shown in an advertisement, including button labels and screen titles, must match this verified walkthrough, not paraphrased wording."
  - "If a feature could not be independently verified on-device (e.g. component-warranty sub-fields, in-app advertising, backup/restore), it is omitted from approved_features rather than inferred."

last_verified_at: "2026-08-29T18:21:00+05:00"
verification_status: "VERIFIED_ON_DEVICE"
verification_note: >-
  Verified by installing the exact campaign APK (products/warranty-vault/apk/android-app-release.apk)
  on an AdForge worker emulator and walking through onboarding, dashboard,
  add-product (all three tabs), product detail, product list, Expiring Soon,
  and Settings screens. This supersedes the two source-derived drafts
  (truthmap1.md, truthmap2.md), which described features not confirmed
  on-screen (e.g. paraphrased evidence text) or used a status value
  (REQUIRES_VERIFICATION) not accepted by the AdForge schema validator.
  Campaign copy/brief direction from truthmap2 (tone, emotional flow,
  taglines) remains a useful creative reference but is not part of this
  truth file's compliance-checked claims.
---
