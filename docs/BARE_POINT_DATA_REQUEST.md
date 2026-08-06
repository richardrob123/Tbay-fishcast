# Bare Point WTP intake-temperature — records request (ready to send)

**Why:** the single highest-leverage accuracy upgrade for the forecast is a real,
continuous, *in-water nearshore* temperature at Thunder Bay. The Bare Point Water
Treatment Plant draws raw lake water through an offshore intake and its SCADA system
logs raw-water temperature continuously — an ideal in-situ calibration/validation sensor
that no public feed provides. This is a routine, non-sensitive operational record.

**Route:** start with a **direct/informal email** (fastest, often free). If that stalls,
file a formal **MFIPPA** (Municipal Freedom of Information and Protection of Privacy Act)
request with the City Clerk. Contacts: City of Thunder Bay — Environment Division /
Water Treatment (`infrastructure@thunderbay.ca`, `waterreport@thunderbay.ca`) or the City
Clerk's FOI office (`clerks@thunderbay.ca`). Verify current addresses on thunderbay.ca.

---

## Draft email

> **Subject:** Data request — Bare Point WTP raw-water intake temperature (research use)
>
> Hello,
>
> I'm developing a free, non-commercial water-temperature forecast for shore anglers on
> the Thunder Bay shoreline of Lake Superior, and I'm looking for an in-water reference
> to validate it. The Bare Point Water Treatment Plant's raw-water intake would be ideal.
>
> Would it be possible to obtain the following, as a machine-readable export (CSV/Excel):
>
> 1. **Raw-water intake temperature** time series — the longest history available, at
>    whatever native logging interval the SCADA system records (hourly or finer preferred;
>    daily is still very useful). 2024 to present especially.
> 2. The **depth and approximate location (lat/long or distance offshore)** of the intake
>    where that temperature is measured.
> 3. The **units and any sensor/QA notes** (sensor type, accuracy, known outages).
>
> This is raw operational temperature data only — nothing about treatment, chemistry,
> security, or operations. I'm happy to receive it in any format you already produce, to
> credit the City of Thunder Bay as the data source, and to share the resulting forecast.
>
> If a formal Municipal Freedom of Information (MFIPPA) request is the right channel,
> please let me know and I'll file one. Thank you very much for considering this.
>
> [Your name / contact]

---

## If formal MFIPPA is required
- Requestor info + $5 application fee (standard).
- **Record requested:** "All raw-water intake temperature readings logged by the Bare
  Point Water Treatment Plant SCADA/historian system, from [start date] to present, in a
  machine-readable format (CSV), together with the intake depth and location and the
  sensor's units and metadata."
- Scope it to *temperature only* to keep it fast, cheap, and clearly non-sensitive.

## When it arrives — how we use it
Wire it as `ingest/bare_point.py`, then it becomes the **local calibration anchor**: it
(a) collapses the ±band by pinning the nearshore column, (b) lets day-ahead *beat*
persistence (the audit's noted gap), and (c) is the ground truth to validate the Landsat
skin-temp and the LSOFS depth-bias correction against. It is the one dataset that turns
the product from "spatial + trend" into a locally-calibrated instrument.
