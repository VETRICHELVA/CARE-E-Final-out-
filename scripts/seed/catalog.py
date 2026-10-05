"""The CARE-E product catalog (S04): plain data, loaded by the hub's `seed_catalog()`.

Rules that keep the chat-ordering evals valid (services/ai-service/evals): exactly two
product names contain "Kit" (Surgical Kit A and Rapid Diagnostic Kit, the two that plain
"kits" must match); "Surgical", "Rapid", "Diagnostic", "Cannula" and "20G" each appear in
exactly one name; and the three demo products have no siblings. "SK-A" etc. are synonyms
(S17).
Every cold-chain product here is stored at 2-8 °C.
"""

# code, name, category, unit, requires_cold_chain, default_min_shelf_life_days
PRODUCTS: list[tuple[str, str, str, str, bool, int]] = [
    # Surgical
    ("SURG-KIT-A", "Surgical Kit A", "Surgical", "kit", False, 30),
    ("SURG-GLV-STR", "Sterile Gloves", "Surgical", "pair", False, 30),
    ("SURG-BLD-22", "Scalpel Blade No. 22", "Surgical", "box", False, 30),
    ("SURG-SUT-ABS", "Absorbable Suture 2-0", "Surgical", "box", False, 30),
    ("SURG-DRP-STR", "Sterile Drape", "Surgical", "each", False, 30),
    # Diagnostics
    ("DIAG-RDK", "Rapid Diagnostic Kit", "Diagnostics", "kit", True, 60),
    ("DIAG-GLU-STR", "Blood Glucose Test Strips", "Diagnostics", "box", False, 30),
    ("DIAG-URN-DIP", "Urine Dipsticks", "Diagnostics", "box", False, 30),
    ("DIAG-ECG-ELC", "ECG Electrodes", "Diagnostics", "pack", False, 30),
    ("DIAG-EDTA-TUB", "EDTA Blood Collection Tube", "Diagnostics", "box", False, 30),
    ("DIAG-BLD-CUL", "Blood Culture Bottle", "Diagnostics", "each", False, 30),
    # IV and infusion
    ("IV-CAN-20G", "IV Cannula 20G", "IV and infusion", "each", False, 30),
    ("IV-SET-INF", "IV Infusion Set", "IV and infusion", "each", False, 30),
    ("IV-NS-500", "Normal Saline 0.9% 500 ml", "IV and infusion", "bottle", False, 30),
    ("IV-RL-500", "Ringer Lactate 500 ml", "IV and infusion", "bottle", False, 30),
    ("IV-STC-3W", "Three-Way Stopcock", "IV and infusion", "each", False, 30),
    # Injection
    ("INJ-SYR-5", "Disposable Syringe 5 ml", "Injection", "each", False, 30),
    ("INJ-NDL-HYP", "Hypodermic Needles", "Injection", "box", False, 30),
    ("INJ-SYR-INS", "Insulin Syringe", "Injection", "each", False, 30),
    ("INJ-NDL-SPN", "Spinal Needle", "Injection", "each", False, 30),
    # PPE
    ("PPE-N95", "N95 Respirator", "PPE", "each", False, 30),
    ("PPE-MSK-3PLY", "Three-Ply Face Mask", "PPE", "box", False, 30),
    ("PPE-GLV-NIT", "Nitrile Examination Gloves", "PPE", "box", False, 30),
    ("PPE-GWN-ISO", "Isolation Gown", "PPE", "each", False, 30),
    ("PPE-FSH", "Face Shield", "PPE", "each", False, 30),
    # Wound care
    ("WND-GZE-STR", "Sterile Gauze Swabs", "Wound care", "pack", False, 30),
    ("WND-BND-CRP", "Crepe Bandage 10 cm", "Wound care", "roll", False, 30),
    ("WND-TAP-MIC", "Microporous Tape", "Wound care", "roll", False, 30),
    ("WND-DRS-FOAM", "Foam Dressing", "Wound care", "each", False, 30),
    # Respiratory
    ("RSP-O2-MSK", "Oxygen Face Mask", "Respiratory", "each", False, 30),
    ("RSP-NSL-PRG", "Nasal Oxygen Prongs", "Respiratory", "each", False, 30),
    ("RSP-ETT-75", "Endotracheal Tube 7.5", "Respiratory", "each", False, 30),
    ("RSP-NEB-MSK", "Nebulizer Mask", "Respiratory", "each", False, 30),
    # Catheters and drainage
    ("CTH-FOL-16", "Foley Catheter 16Fr", "Catheters and drainage", "each", False, 30),
    ("CTH-URB-2L", "Urine Drainage Bag 2 L", "Catheters and drainage", "each", False, 30),
    # Cold-chain medicines
    ("MED-INS-REG", "Regular Insulin 10 ml", "Medicines", "vial", True, 30),
    ("MED-OXY-5IU", "Oxytocin 5 IU/ml", "Medicines", "ampoule", True, 30),
    ("MED-TT-VAC", "Tetanus Toxoid Vaccine", "Vaccines", "vial", True, 30),
    ("MED-HEPB-VAC", "Hepatitis B Vaccine", "Vaccines", "vial", True, 30),
    ("MED-ARV-VAC", "Anti-Rabies Vaccine", "Vaccines", "vial", True, 30),
]

COLD_CHAIN_C = (2.0, 8.0)
