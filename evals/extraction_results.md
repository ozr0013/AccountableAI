# Extraction results: messy raw files back to documents

The generator's clean documents are rendered as the paperwork a controller really gets
(`world/mess`), then read back by `agents/extract` and compared field by field with the truth
(`python -m evals.extraction`). End to end means the same 9 scenarios and the judge moment run on
the parsed documents instead of the clean ones (`python -m evals.harness --messy`).

## Five seeds (13, 21, 34, 55, 89), ~305 raw files each

| Reader | Documents fully correct | Fields correct | Asked a person | Scenarios 9/9 + moment, end to end | Model calls (cost) |
|---|---|---|---|---|---|
| Rules only (no OCR, no model) | 1601/1650 (97.0%) | 10265/10335 (99.3%) | 112 | 5/5 seeds | 0 |
| Rules + tesseract OCR | 1701/1710 (99.5%) | 10842/10851 (99.9%) | 12 | 5/5 seeds | 0 |
| Rules + OCR + Haiku re-read | 1709/1710 (99.9%) | 10874/10875 (99.99%) | 5 | 5/5 seeds | 4 ($0.0012) |

"Asked a person" includes one vendor credit memo per seed, which always goes to a person.
Rules-only misses are scans it can't read (flagged, never guessed) and what cascades from them:
a bank line or a "we got paid" email whose invoice is a scan can't be tied to anything, so it is
flagged too. No miss produced a false alert on Keyway in any seed.

**Honest caveat:** the same team wrote the mess and the parser, so these numbers are an upper
bound and a regression test, not a claim about real-world paperwork. Real inboxes will have
layouts we never generated; the design answer is that every record carries its method,
confidence and warnings, and anything the rules can't settle goes to a person or the model.

## Seed 13 in detail, rules only

302 raw files (emails with PDF attachments, scans, bank CSVs, wire PDFs, statements, junk mail). Rules-first parser, no model calls unless a scan needs OCR.

| Metric | Result |
|---|---|
| Documents fully correct (every field) | 315/326 (96.6%) |
| Fields correct | 2013/2031 (99.1%) |
| Junk and duplicate copies correctly ignored | 6/6 |
| Unreadable scans flagged for a person (no OCR) | 16/16 |
| Records flagged for a person in total | 28 |
| How text was read | csv: 9, email: 107, none: 16, text_layer: 217 |

### By document type

| Type | Fully correct |
|---|---|
| agreement | 1/1 |
| funding | 109/109 |
| manager_bank | 6/9 |
| manager_reply | 1/1 |
| manager_statement | 9/9 |
| vendor_confirmation | 93/101 |
| vendor_invoice | 94/94 |
| vendor_notice | 2/2 |

### By kind of mess

| Mess | Fully correct |
|---|---|
| (none) | 87/87 |
| claim_without_proof | 1/1 |
| combined_payment | 1/2 |
| filler_rows | 6/9 |
| html_only_email | 10/14 |
| id_only_in_quoted_text | 1/1 |
| lookalike_sender_domain | 1/1 |
| no_invoice_ref_amount_only | 49/57 |
| no_keyway_ref | 5/5 |
| no_received_date | 1/1 |
| no_vendor_payment_in_period | 2/2 |
| one_confirmation_two_invoices | 4/4 |
| one_wire_many_invoices | 79/79 |
| partial_payment | 1/1 |
| prior_balance_current | 2/2 |
| prior_balance_pastdue | 3/3 |
| status_word_variants | 9/9 |
| table_in_plain_text | 1/1 |
| terms_disagree_with_agreement | 40/40 |
| vendor_name_variant | 55/55 |

### Misses

- doc-bank-keyway-2026-01-ops (bank/FPB_Operating_20260106_20260205_01-ops.csv): payments
- doc-bank-keyway-2026-02-ops (bank/FPB_Operating_20260130_20260305_02-ops.csv): payments
- doc-bank-keyway-2026-03-ops (bank/FPB_Operating_20260301_20260405_03-ops.csv): payments
- doc-conf-inv-kw-1014 (inbox/20260406_1418_7cd43e.eml): found
- doc-conf-inv-kw-1018 (inbox/20260131_1015_a267cd.eml): found
- doc-conf-inv-kw-1020 (inbox/20260329_1155_11df23.eml): found
- doc-conf-inv-kw-1039 (inbox/20260205_1539_cd5674.eml): found
- doc-conf-inv-kw-1057 (inbox/20260318_0848_fc7274.eml): found
- doc-conf-inv-kw-1069 (inbox/20260316_1150_73b95a.eml): found
- doc-conf-inv-kw-1089 (inbox/20260213_1050_c65322.eml): found
- doc-conf-inv-kw-1091 (inbox/20260308_1010_e053ff.eml): found

### What the parser asked a person about

- `bank/FPB_Operating_20260106_20260205_01-ops.csv`: one 4,298.55 payment covers INV-KW-1049, INV-KW-1066; 'CHECK 10726 YARDPRO LNDSCP' has no reference; matched to INV-KW-1051 by vendor and amount; 'BILL PAY HANDY CREW RPRS' has no reference; matched to INV-KW-1055 by vendor and amount; 'CHECK 10680 IRONPIPE PLBG' has no reference; matched to INV-KW-1100 by vendor and amount; 'CHECK 10695 HANDY CREW RPRS' has no reference; matched to INV-KW-1073 by vendor and amount; 'ACH DEBIT COOLAIR HVAC INC' has no reference; matched to INV-KW-1084 by vendor and amount; 'ONLINE PMT HANDY CREW RPRS' has no reference; matched to INV-KW-1041 by vendor and amount; 'CHECK 10855 BREEZEWAY HVAC' has no reference; matched to INV-KW-1037 by vendor and amount; 'BILL PAY GREENEDGE LNDSCP' has no reference; matched to INV-KW-1067 by vendor and amount; 'CHECK 10813 SUMMIT HTG AIR' has no reference; matched to INV-KW-1024 by vendor and amount; can't tie 'CHECK 10686 YARDPRO LNDSCP' (4,004.10) to one invoice (0 candidates); 'CHECK 10518 YARDPRO LNDSCP' has no reference; matched to INV-KW-1065 by vendor and amount; 'CHECK 10659 PEAK ROOFING CO' has no reference; matched to INV-KW-1012 by vendor and amount; 'CHECK 10559 GREENEDGE LNDSCP' has no reference; matched to INV-KW-1003 by vendor and amount; 'CHECK 10576 BRIGHTSIDE CLN' has no reference; matched to INV-KW-1077 by vendor and amount; 'ACH PMT BREEZEWAY HVAC' has no reference; matched to INV-KW-1053 by vendor and amount
- `bank/FPB_Operating_20260130_20260305_02-ops.csv`: can't tie 'CHECK 10309 TOPLINE ROOFNG' (2,194.90) to one invoice (0 candidates); 'ACH DEBIT LAWN LOGIC LLC' has no reference; matched to INV-KW-1031 by vendor and amount; 'ONLINE PMT FRESH START CLN' has no reference; matched to INV-KW-1061 by vendor and amount; 'BILL PAY PEAK ROOFING CO' has no reference; matched to INV-KW-1017 by vendor and amount; 'ONLINE PMT LAWN LOGIC LLC' has no reference; matched to INV-KW-1021 by vendor and amount; 'ACH DEBIT PEAK ROOFING CO' has no reference; matched to INV-KW-1052 by vendor and amount; 'ACH DEBIT FRESH START CLN' has no reference; matched to INV-KW-1030 by vendor and amount; 'CHECK 10593 YARDPRO LNDSCP' has no reference; matched to INV-KW-1070 by vendor and amount; 'ACH PMT BRIGHTSIDE CLN' has no reference; matched to INV-KW-1036 by vendor and amount; 'ACH PMT GREENEDGE LNDSCP PPD ID:5294090121' has no reference; matched to INV-KW-1002 by vendor and amount; 'ONLINE PMT Rio Grande Plumb' has no reference; matched to INV-KW-1033 by vendor and amount; 'CHECK 10414 Rio Grande Plumb' has no reference; matched to INV-KW-1026 by vendor and amount; can't tie 'BILL PAY FRESH START CLN PPD ID:4328350827' (365.55) to one invoice (0 candidates); 'ACH PMT SHINGLEMASTERS PPD ID:1196830969' has no reference; matched to INV-KW-1050 by vendor and amount; 'CHECK 10223 SUMMIT HTG AIR' has no reference; matched to INV-KW-1095 by vendor and amount; 'CHECK 10546 CLEARFLOW DRAINS' has no reference; matched to INV-KW-1096 by vendor and amount
- `bank/FPB_Operating_20260301_20260405_03-ops.csv`: 'CHECK 10690 FRESH START CLN' has no reference; matched to INV-KW-1082 by vendor and amount; can't tie 'ONLINE PMT SUMMIT HTG AIR' (2,888.65) to one invoice (0 candidates); 'BILL PAY PAINTPRO PNTG PPD ID:1740554847' has no reference; matched to INV-KW-1068 by vendor and amount; 'ACH DEBIT TOPLINE ROOFNG' has no reference; matched to INV-KW-1028 by vendor and amount; can't tie 'ACH PMT HANDY CREW RPRS PPD ID:1981364361' (3,828.00) to one invoice (0 candidates); 'CHECK 10965 FRESH START CLN' has no reference; matched to INV-KW-1094 by vendor and amount; 'ONLINE PMT BREEZEWAY HVAC PPD ID:5272694701' has no reference; matched to INV-KW-1007 by vendor and amount; can't tie 'ACH DEBIT LAWN LOGIC LLC' (608.65) to one invoice (0 candidates); 'CHECK 10686 IRONPIPE PLBG' has no reference; matched to INV-KW-1062 by vendor and amount; 'ACH DEBIT YARDPRO LNDSCP' has no reference; matched to INV-KW-1010 by vendor and amount; 'ACH PMT FLOWFIX PLMBG' has no reference; matched to INV-KW-1045 by vendor and amount; 'BILL PAY LAWN LOGIC LLC' has no reference; matched to INV-KW-1004 by vendor and amount; 'CHECK 10994 FRESH START CLN' has no reference; matched to INV-KW-1059 by vendor and amount; 'ONLINE PMT PEAK ROOFING CO' has no reference; matched to INV-KW-1009 by vendor and amount; can't tie 'ACH DEBIT FRESH START CLN' (1,622.85) to one invoice (0 candidates); can't tie 'ACH DEBIT SHINGLEMASTERS PPD ID:2450675051' (1,184.10) to one invoice (0 candidates); can't tie 'ONLINE PMT TOPLINE ROOFNG PPD ID:2675894119' (2,435.00) to one invoice (0 candidates); can't tie 'CHECK 10352 TIDYHOME CLNRS' (2,266.55) to one invoice (0 candidates)
- `inbox/20260119_0725_d415e2.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260123_0701_07db2f.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260127_1124_738c04.eml`: vendor credit memo ($75.00): check it is applied to Oakline's next funding
- `inbox/20260127_1254_e08211.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260131_1015_a267cd.eml`: can't tell which invoice this is about
- `inbox/20260205_1539_cd5674.eml`: can't tell which invoice this is about
- `inbox/20260208_1738_ccd2da.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260213_1050_c65322.eml`: can't tell which invoice this is about
- `inbox/20260218_1946_a2d19a.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260228_1152_0bd717.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260301_1916_291d90.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260302_1227_e89130.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260308_1010_e053ff.eml`: can't tell which invoice this is about
- `inbox/20260309_1543_69f72a.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260311_2324_e9116a.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260312_2331_6c9ea1.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260315_0713_f7d9f2.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260316_1150_73b95a.eml`: can't tell which invoice this is about
- `inbox/20260318_0848_fc7274.eml`: can't tell which invoice this is about
- `inbox/20260321_0549_2b067a.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260323_1311_43fe0b.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260327_2254_b16c8e.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260329_1155_11df23.eml`: can't tell which invoice this is about
- `inbox/20260329_1505_8a854d.eml`: scanned image with no text layer and no OCR available: ask for a clean copy
- `inbox/20260406_1418_7cd43e.eml`: can't tell which invoice this is about
