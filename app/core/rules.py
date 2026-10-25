"""Fixed rules for the deterministic calculator.

Every constant here is part of the pre-registered method: change it only with a
new git tag, never to make a particular case come out right.
"""

# Biomarkers that human genetics shows are NOT causal for coronary disease, so a drug whose
# main action is to move them is expected to change nothing (reference ids in parentheses).
# hsCRP is deliberately absent: it is non-causal itself but marks an inflammatory pathway that
# may be causal, so a drug "intended" to lower CRP is unquantified, not "wrong biomarker".
NON_CAUSAL_BIOMARKERS = {
    "HDL-C",             # hdl_genetic
    "Lp-PLA2 activity",  # lppla2_genetic
    "sPLA2-IIA",         # spla2_genetic
}

# Biomarkers with a trial-scale benefit coefficient the calculator can use.
QUANTIFIED_BIOMARKERS = {"LDL-C", "non-HDL-C", "apoB", "Lp(a)", "SBP", "triglycerides"}

# Which measured values size a drug whose main mechanism is this biomarker. LDL-C, non-HDL-C and apoB measure the
# same atherogenic particles; a triglyceride drug acts on TG-rich particles that only non-HDL-C and apoB count.
# "" = no intended biomarker named: any sized value will do.
_APOB_PARTICLES = ("LDL-C", "non-HDL-C", "apoB")
MEASURED_BY = {"LDL-C": _APOB_PARTICLES, "non-HDL-C": _APOB_PARTICLES, "apoB": _APOB_PARTICLES,
               "triglycerides": ("non-HDL-C", "apoB"), "Lp(a)": ("Lp(a)",), "SBP": ("SBP",),
               "": _APOB_PARTICLES + ("Lp(a)", "SBP")}

# The calculator only sizes a drug whose MAIN mechanism is quantified (or proven non-causal).
# Anything else - GLP-1 (weight, glucose), SGLT2 (glucose, haemodynamics), colchicine or
# anti-cytokines (inflammation) - is "not computable": partial lipid/BP effects are shown as an
# indicative number only, because they would understate the drug and predict a false failure.
UNQUANTIFIED_CAUSAL_PATHWAYS = {"IL-1beta", "IL-6", "inflammation"}

# HbA1c: intensive glucose lowering, OR 0.85 (0.77-0.93) for CHD per ~0.9 percentage points
# (hba1c_ray2009). Causal status for MACE is UNCERTAIN: used only in the indicative estimate.
HBA1C_RR_PER_POINT_UNCERTAIN = 0.85 ** (1 / 0.9)

# Trial-scale LDL benchmark (CTT 2010): RR per 1 mmol/L LDL-C.
LDL_RR_PER_MMOL_5Y = 0.78      # ldl_ctt_5y
LDL_RR_PER_MMOL_YEAR1 = 0.88   # ldl_ctt_year1

# Lp(a) -> LDL-equivalent (Burgess 2018): 101.5 mg/dL Lp(a) ~ 1 mmol/L LDL-C.
LPA_MGDL_PER_LDL_MMOL = 101.5  # lpa_genetic / lpa_trial_projected

# Lp(a) nmol/L -> mg/dL. ASSUMPTION: no fixed conversion exists (depends on isoform size);
# 2.4 is a commonly used approximation (EAS consensus calls any fixed factor a "best guess").
LPA_NMOL_PER_MGDL = 2.4

# apoB -> LDL-equivalent. ASSUMPTION, to verify against a primary source:
# slope between ESC/EAS 2019 paired targets (LDL-C 1.4 / 1.8 / 2.6 mmol/L
# vs apoB 65 / 80 / 100 mg/dL) = 35 / 1.2 = ~29 mg/dL apoB per mmol/L LDL-C.
APOB_MGDL_PER_LDL_MMOL = 29.2

# Systolic BP (Ettehad 2016): RR 0.80 per 10 mmHg reduction.
SBP_RR_PER_10MMHG = 0.80       # sbp_trial_ettehad

MGDL_PER_MMOL_CHOLESTEROL = 38.67

# Typical baselines used ONLY when a source gives a percent change without a
# baseline. Always reported as an assumption in the output.
DEFAULT_BASELINE = {"LDL-C": 2.0, "non-HDL-C": 2.6, "apoB": 80.0}  # mmol/L, mmol/L, mg/dL

# If LDL-C and apoB percent changes differ by more than this ratio, the LDL
# assay is treated as unreliable and apoB is used (e.g. CETP inhibitors).
DISCORDANCE_RATIO = 1.5
# ...and only when they also differ by at least this many percentage points (CETP inhibitors: 20 or more), so a
# small or rounded value ("minimal effect" written as 0%) cannot switch the measure.
DISCORDANCE_MIN_POINTS = 10.0

# Below this expected relative risk reduction no feasible outcome trial can
# detect the effect: the causal part is treated as negligible.
NEGLIGIBLE_RRR = 0.05

# Probability-of-success bands (forecast) and power cut-offs (autopsy).
LIKELY_SUCCESS_POWER = 0.70
BORDERLINE_POWER = 0.40
ROBUST_SUCCESS_RRR = 0.15
ROBUST_SUCCESS_POWER = 0.85
