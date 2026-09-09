from pathlib import Path
import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
cells = []


def md(source):
    cells.append(nbf.v4.new_markdown_cell(source.strip()))


def code(source):
    cells.append(nbf.v4.new_code_cell(source.strip()))


md(r'''
# Part B · Airport supply

**Decision:** Should Rapido fund a captain acquisition push around the airport, including a sign-up bonus?

The evidence says **no blanket catchment acquisition yet**. The airport has a severe, repeatable night shortage, but the sampled trips show that a captain leaving the airport is often stranded without a quick return fare—especially after suburban trips. Acquire-and-bonus would add headcount without fixing the utilization and deadhead loop. First test timed availability, airport dispatch/queueing, and return-fare matching; use acquisition only if that pilot proves incremental fulfilled demand at acceptable unit economics.

This notebook covers B1–B3 only. Airport hourly marketplace data and sampled trip data have different denominators and are never multiplied together. The hourly file measures demand/supply state; the trip file is a sample of airport-origin trips and does not identify unique captains.

**Analysis contract**

- Treat all timestamp strings as **Asia/Kolkata**, with the stated 30 June 2026 23:59 IST extraction cutoff.
- B1 uses airport-terminal zones only. Fulfilment is `sum(fulfilled_requests) / sum(requests)`; never average row-level ratios when request volumes differ.
- Discover the critical window from May, then validate it in June: hours with May fulfilment below 50%. The discovered 21:00–04:00 window is fixed before reading June results.
- B2 uses the sampled trip rows. A 24-hour end buffer is the primary return-fare comparison because the file has no completion timestamp; 0/6/48-hour sensitivity is shown.
- A returned fare means `got_return_fare_within_20min == 1` in the sample. It does not establish captain earnings, completion, or unique-captain retention.
- B3 sizes a **timed pilot** from observed unfulfilled demand. Roster, attendance, productivity, incentive and fallback amounts are planning assumptions, not observed costs.
- No deep-learning model is appropriate here: the inputs are aggregated time series and tabular trip flags, with no unique-captain panel or flight schedule to support a predictive model. The decision needs rates, uncertainty, and a controlled intervention.
''')

code(r'''
from pathlib import Path
import json, hashlib, platform, importlib.metadata
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import display, Markdown, Image as IPImage

from utils import (ROOT, DATA_DIR, AIRPORT_OUTPUT_DIR, CUTOFF, CRITICAL_HOURS,
                   PERIODS, load_airport_data, analyse_airport_marketplace,
                   analyse_airport_trips, airport_intervention_scenarios,
                   airport_input_fingerprint)

OUT = ROOT / 'outputs' / 'part_b'
OUT.mkdir(parents=True, exist_ok=True)
SEED = 20260630
BOOTSTRAPS = 2000
pd.set_option('display.max_columns', 20)
pd.set_option('display.max_rows', 60)
pd.set_option('display.float_format', lambda x: f'{x:,.3f}')
sns.set_theme(style='whitegrid', context='notebook', palette='colorblind')
plt.rcParams.update({'figure.dpi': 120, 'savefig.dpi': 180,
                     'axes.spines.top': False, 'axes.spines.right': False})
BLUE, ORANGE, GREEN, GREY = '#245B78', '#DF803C', '#26836C', '#8A969D'
tables, figures = {}, {}

def show_table(name, frame):
    tables[name] = frame.copy()
    display(frame)

def save_figure(name, fig):
    path = OUT / f'{name}.png'
    fig.savefig(path, bbox_inches='tight')
    figures[name] = str(path)
    display(IPImage(filename=str(path)))
    plt.close(fig)
''')

md(r'''
## Data audit · two files, two denominators

The audit checks the hourly grid, internal demand arithmetic, zone types, trip flag domains, date range, and duplicate IDs. It also flags where the sampled trip count exceeds marketplace fulfilled/request counts in the same terminal-hour: that is evidence that the trip extract is not a validated subset of the hourly marketplace rows. We retain the sample for directional economics and do not scale its rates by hourly requests.
''')
code(r'''
airport = load_airport_data(DATA_DIR)
hourly, trips = airport.hourly, airport.trips
for name, frame in airport.tables.items():
    show_table(name, frame)
display(Markdown(f"The two airport terminals cover **{hourly.hour_ts.nunique():,} hourly timestamps each** across "
                 f"**{hourly.date.nunique()} calendar days**. The trip file contains **{len(trips):,} sampled rows** "
                 f"and no captain identifier. Its 24-hour return comparison therefore needs both an end buffer and a "
                 "sample-denominator disclaimer."))
''')

md(r'''
## B1 · Demand–supply mismatch: when and how much

The critical window is discovered from May and then held fixed for June validation. Hourly fulfilment is compared with the daytime operating baseline. The practical unit is an **unfulfilled request per calendar day**, while the diagnostic supply signal is online captains per terminal-hour.
''')
code(r'''
market_tables, market = analyse_airport_marketplace(airport, bootstraps=BOOTSTRAPS, seed=SEED)
for name, frame in market_tables.items():
    show_table(name, frame)
show_table('marketplace_summary', pd.DataFrame([market]))

period = market_tables['period_summary'].copy()
fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
axes[0].bar(period.period, period.fulfilment_rate * 100, color=[ORANGE, GREEN, BLUE])
axes[0].set_ylabel('Fulfilment rate (%)')
axes[0].set_title('Airport terminal fulfilment by operating period')
axes[0].tick_params(axis='x', rotation=20)
for i, value in enumerate(period.fulfilment_rate * 100):
    axes[0].text(i, value + 2, f'{value:.1f}%', ha='center', fontsize=9)
axes[1].bar(period.period, period.unfulfilled_per_calendar_day, color=[ORANGE, GREEN, BLUE])
axes[1].set_ylabel('Unfulfilled requests per calendar day')
axes[1].set_title('The critical window carries the shortage')
axes[1].tick_params(axis='x', rotation=20)
fig.tight_layout(); save_figure('airport_period_mismatch', fig)

profile = market_tables['hour_profile'].copy()
fig, ax1 = plt.subplots(figsize=(11, 4.2))
ax1.plot(profile.hour, profile.fulfilment_rate * 100, marker='o', color=BLUE, label='Fulfilment rate')
ax1.set_xlabel('Hour of day (IST)'); ax1.set_ylabel('Fulfilment rate (%)')
ax1.set_xticks(range(24)); ax1.set_ylim(0, 105)
ax2 = ax1.twinx()
ax2.bar(profile.hour, profile.unfulfilled / profile.zone_hours, alpha=.22, color=ORANGE,
        label='Unfulfilled per terminal-hour')
ax2.set_ylabel('Unfulfilled requests per terminal-hour')
ax1.axvspan(20.5, 23.5, color=ORANGE, alpha=.08); ax1.axvspan(-.5, 3.5, color=ORANGE, alpha=.08)
fig.suptitle('Supply collapses in the 21:00–04:00 airport window')
fig.tight_layout(); save_figure('airport_hourly_profile', fig)

monthly = market_tables['monthly_period_summary']
critical_monthly = monthly.loc[monthly.period.eq(PERIODS[0])]
fig, ax = plt.subplots(figsize=(8, 3.8))
ax.plot(critical_monthly.month, critical_monthly.fulfilment_rate*100, marker='o', color=BLUE)
ax.set_ylim(0, 60); ax.set_ylabel('Critical-window fulfilment (%)')
ax.set_title('The shortage is stable across May and June')
for i, value in enumerate(critical_monthly.fulfilment_rate*100): ax.text(i, value+2, f'{value:.1f}%', ha='center')
fig.tight_layout(); save_figure('airport_month_validation', fig)

display(Markdown(f"**B1 readout:** in the 21:00–04:00 window, only **{market['critical_fulfilment']:.1%}** of "
    f"{market['critical_requests']:,} requests were fulfilled, leaving **{market['critical_unfulfilled']:,}** "
    f"unfulfilled (**{market['critical_unfulfilled_per_day']:.0f}/day**, **{market['critical_unfulfilled_share']:.1%}** "
    f"of the terminal's total unfulfilled requests). Daytime fulfilment is **{market['daytime_fulfilment']:.1%}** "
    f"with **{period.loc[period.period.eq(PERIODS[1]), 'unfulfilled_per_calendar_day'].iloc[0]:.0f}/day** unfulfilled. "
    f"Online supply averages **{market['critical_mean_online_per_terminal_hour']:.1f}** captains per terminal-hour "
    f"in the critical window versus **{market['daytime_mean_online_per_terminal_hour']:.1f}** daytime; "
    f"request-weighted ETA is **{market['critical_eta']:.1f}** vs **{period.loc[period.period.eq(PERIODS[1]), 'request_weighted_eta_min'].iloc[0]:.1f}** minutes "
    f"and surge is **{market['critical_surge']:.2f}×** vs **{period.loc[period.period.eq(PERIODS[1]), 'request_weighted_surge'].iloc[0]:.2f}×**. "
    f"May and June critical fulfilment are **{market['may_critical_fulfilment']:.1%}** and **{market['june_critical_fulfilment']:.1%}**, respectively."))
''')

md(r'''
## B2 · What happens after an airport trip?

The trip sample changes the diagnosis. We compare destination types using an explicit 24-hour buffer before the extraction cutoff; the buffer does not fix the missing completion timestamp, so 0/6/48-hour sensitivity and calendar-day bootstrap intervals are included. We report cancellation and return-fare outcomes separately. Reported fare is not captain earnings.
''')
code(r'''
trip_tables, trip_summary = analyse_airport_trips(airport, buffer_hours=24, bootstraps=BOOTSTRAPS, seed=SEED)
for name, frame in trip_tables.items():
    show_table(name, frame)
show_table('trip_summary', pd.DataFrame([trip_summary]))

outcomes = trip_tables['destination_sample_outcomes'].copy()
plot_outcomes = outcomes.set_index('drop_zone_type')[['cancel_rate','next_fare_rate']]
fig, ax = plt.subplots(figsize=(9, 4.3))
plot_outcomes.mul(100).plot.bar(ax=ax, color=[ORANGE, GREEN])
ax.set_ylabel('Rate (%)'); ax.set_title('Airport-origin trip outcomes by destination type')
ax.legend(['Captain cancelled','Return fare within 20 min'], loc='upper right')
ax.tick_params(axis='x', rotation=0)
fig.tight_layout(); save_figure('airport_trip_outcomes', fig)

display(Markdown(f"**B2 readout:** the 24-hour-buffer sample has **{trip_summary['sample_rows']:,}** rows and "
    f"**{trip_summary['return_eligible_records']:,}** return-fare-eligible rows. Suburban trips show "
    f"**{trip_summary['suburban_cancel_rate']:.1%}** cancellation versus **{trip_summary['core_cancel_rate']:.1%}** "
    f"for city-core trips: a raw gap of **{(trip_summary['suburban_cancel_rate']-trip_summary['core_cancel_rate'])*100:.1f} pp** "
    f"and a time/distance-standardized gap of **{trip_summary['standardized_cancel_gap_pp']:.1f} pp** over "
    f"**{trip_summary['standardization_coverage']:.1%}** of the shared strata. Only **{trip_summary['suburban_next_fare_rate']:.1%}** "
    f"of suburban trips receive a return fare within 20 minutes versus **{trip_summary['core_next_fare_rate']:.1%}** "
    f"for city-core trips (gap **{(trip_summary['suburban_next_fare_rate']-trip_summary['core_next_fare_rate'])*100:.1f} pp**). "
    f"Suburban trips average **{trip_summary['suburban_mean_distance_km']:.1f} km** and reported fare **INR {trip_summary['suburban_reported_fare']:.0f}**, "
    f"versus **{trip_summary['core_reported_fare']:.0f}** for the core. This supports a deadhead/return-loop problem, "
    "but not a causal claim about destination choice or earnings."))
''')

md(r'''
## B3 · Answer the acquisition question

**Recommendation: do not fund a blanket airport-catchment signup bonus now.** The shortage is real, but the observed shortage is time-specific and the post-trip economics are destination-specific. A bonus may buy captains who are online during the wrong hours, or worsen supply after the airport trip if suburban return probability stays low.

Run a 28-night, zone-hour randomized pilot first: recruit or schedule a small roster for the 21:00–04:00 window, improve airport queue/dispatch, and test return-fare matching or a bounded deadhead fallback. Keep an untreated comparable set of terminal-hours. Primary outcomes are incremental fulfilled requests and unfulfilled requests per terminal-hour; guardrails are ETA, surge, cancellation, return-fare rate, complaints and captain retention/attendance.

The pilot scenario below is intentionally transparent: a 20-slot roster, 80% attendance, 0.75 completions per effective captain-hour, INR 30/hour availability incentive, and an INR 60 fallback with a 20-captain nightly cap. These are planning inputs. They size a test; they do not price a permanent program or prove that recruited captains are unique.
''')
code(r'''
pilot_tables, pilot = airport_intervention_scenarios(market, roster=20, attendance=.8,
                                                     hourly_incentive_inr=30., fallback_inr=60., fallback_nightly_cap=20)
for name, frame in pilot_tables.items(): show_table(name, frame)
show_table('pilot_summary', pd.DataFrame([pilot]))

capacity = pilot_tables['capacity_scenarios'].copy()
fig, ax = plt.subplots(figsize=(9, 4.1))
for productivity, g in capacity.groupby('assumed_completions_per_effective_captain_hour'):
    ax.plot(g.recovered_fraction_of_unfulfilled*100, g.seven_hour_roster_slots_at_80pct_attendance,
            marker='o', label=f'{productivity:.2f} completions/effective hour')
ax.axhline(20, color=GREY, linestyle='--', label='20-slot pilot roster')
ax.set(xlabel='Recovered share of critical-window unfulfilled (%)',
       ylabel='Seven-hour roster slots needed', title='Pilot capacity is measurable before acquisition scale-up')
ax.legend(fontsize=8); fig.tight_layout(); save_figure('airport_pilot_capacity', fig)

display(Markdown(f"**B3 decision:** a 10% recovery target equals **{pilot['pilot_extra_fulfilled_per_night']:.0f}** "
    f"additional fulfilled requests per critical night, or **{pilot['pilot_extra_per_30_day_month']:.0f}** per 30-day month. "
    f"The illustrative 20-slot roster has capacity for **{pilot['pilot_capacity_at_assumed_productivity']:.0f}** "
    f"completions/night under the stated assumptions. Expected variable cost is **INR {pilot['pilot_expected_nightly_variable_cost']:,.0f}/night** "
    f"(cap INR {pilot['pilot_max_nightly_variable_cost']:,.0f}), or **INR {pilot['pilot_cost_per_incremental_fulfilment_at_target']:.0f}** "
    f"per incremental fulfilment at the 10% target. A 30-day scenario costs INR {pilot['pilot_expected_30_day_cost']:,.0f}; "
    "these values exclude acquisition bonus, fixed engineering, airport fees and captain earnings. "
    "Only if this timed pilot improves fulfilled demand and return economics should Supply size a catchment acquisition test."))
''')

md(r'''
## Limitations and Monday plan

- The hourly file has no flight schedule, weather, queue position, unique captain IDs, or dispatch/acceptance events. We cannot tell whether the night gap is caused by captain availability, positioning, dispatch policy, or airport access rules.
- The trip file has no unique captain ID, trip completion timestamp, actual earnings, or acquisition channel. Its suburban/core contrast is a sample association, not a causal estimate.
- The sample is not validated as a subset of hourly fulfilled requests in 380 terminal-hours. It must not be scaled to marketplace demand.
- `unfulfilled_requests == 1` occurs in 7,740 zone-hours; verify whether this is a metric floor or a real count.

**Monday:** create a zone-hour availability roster; instrument airport queue entry/exit and exact trip completion; trial return-fare matching and a bounded fallback; randomize terminal-hours over 28 nights; then decide whether any catchment acquisition or bonus is worth funding. Measure incremental fulfilment per online captain-hour, not signups.
''')
code(r'''
summary = {**market, **trip_summary, **pilot}
summary['critical_hours'] = [int(x) for x in CRITICAL_HOURS]
summary['raw_input_fingerprints'] = dict(airport_input_fingerprint(DATA_DIR))
summary['part_b_decision'] = 'No blanket catchment acquisition bonus yet; run a timed supply and return-loop pilot.'
for name, frame in tables.items():
    frame.to_csv(OUT / f'{name}.csv', index=True)
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2, default=lambda x: float(x) if isinstance(x, np.floating) else int(x) if isinstance(x, np.integer) else x), encoding='utf-8')
manifest = {'created_utc': pd.Timestamp.now(tz='UTC').isoformat(), 'python': platform.python_version(),
            'seed': SEED, 'bootstrap_replicates': BOOTSTRAPS, 'cutoff_ist': str(CUTOFF),
            'packages': {p: importlib.metadata.version(p) for p in ['pandas','numpy','scipy','matplotlib']},
            'raw_inputs': airport.manifest}
(OUT / 'run_manifest.json').write_text(json.dumps(manifest, indent=2, default=str), encoding='utf-8')
print(f'Part B checks passed. Exported {len(tables)} tables and {len(figures)} figures to {OUT}.')
''')

code(r'''
from scripts.build_part_b_reports import create_part_b_reports
reports = create_part_b_reports(summary, tables, figures, ROOT / 'deliverables')
from scripts.build_combined_reports import create_combined_reports
combined_reports = create_combined_reports(ROOT)
reports.extend(combined_reports)
for path in reports:
    display(Markdown(f'[{path.name}]({path.relative_to(ROOT).as_posix()})'))
''')

nb = nbf.v4.new_notebook(cells=cells, metadata={
    'kernelspec': {'display_name': 'Python 3 (Part B)', 'language': 'python', 'name': 'python3'},
    'language_info': {'name': 'python'}, 'title': 'Part B — Airport supply'})
nbf.write(nb, ROOT / 'analysis_partB.ipynb')
print(f'Wrote {len(cells)} cells to analysis_partB.ipynb')
