#!/usr/bin/env python3
"""
Synthetic business dataset generator (spec section 63).

Generates a coherent commerce/SaaS business across ~24 months: customers,
products, orders, order_items, refunds, subscriptions, support_tickets,
marketing_campaigns, inventory and payments.

The important property is that the interesting events are *structural, not
labelled*. There is no "revenue_drop" column. Instead the generative process
contains real mechanisms:

  * A support-quality regression begins in the South region in mid-July, which
    raises ticket volume and first-response time there.
  * Enterprise customers in the South respond to that with elevated churn over
    the following 6-10 weeks (with realistic lag, not same-day).
  * Churned enterprise accounts stop placing their recurring high-value orders,
    which is what actually produces the August revenue decline.
  * A competitor price move compresses Product-line A discounts from September.

Nothing downstream is told any of this. The root-cause engine has to find it by
decomposing the revenue delta, and the numbers it reports are whatever the data
actually contains. Run the same generator with a different --seed and the
headline percentages change accordingly.

Usage:
    python scripts/seed_data.py --out ./seed --seed 42 --format csv
    python scripts/seed_data.py --out ./seed --postgres $DATABASE_URL
"""
from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REGIONS = ["North", "South", "East", "West", "Central"]
REGION_WEIGHT = [0.20, 0.31, 0.17, 0.19, 0.13]
SEGMENTS = ["Enterprise", "Mid-Market", "SMB", "Self-Serve"]
SEGMENT_WEIGHT = [0.17, 0.24, 0.34, 0.25]
CHANNELS = ["Direct Sales", "Partner", "Web", "Marketplace"]
CATEGORIES = ["Platform", "Analytics Add-on", "Integrations", "Support Plan", "Hardware"]

SEGMENT_ORDER_VALUE = {
    "Enterprise": (18_000, 6_500),
    "Mid-Market": (4_200, 1_600),
    "SMB": (900, 420),
    "Self-Serve": (180, 90),
}
# Expected orders per customer per month, by segment.
SEGMENT_ORDER_RATE = {"Enterprise": 3.4, "Mid-Market": 1.9, "SMB": 0.95, "Self-Serve": 0.40}


class BusinessSimulator:
    def __init__(
        self,
        seed: int = 42,
        months: int = 24,
        n_customers: int = 4_200,
        incident_start: date = date(2025, 6, 16),
    ) -> None:
        self.rng = np.random.default_rng(seed)
        self.end = date(2025, 12, 31)
        self.start = self.end - timedelta(days=int(months * 30.44))
        self.n_customers = n_customers
        self.dates = pd.date_range(self.start, self.end, freq="D")

        # --- Structural mechanisms (not exposed as columns) -------------------
        self.support_regression_start = incident_start
        self.support_regression_region = "South"
        self.price_pressure_start = date(2025, 9, 1)
        self.price_pressure_category = "Analytics Add-on"

    # -- helpers --------------------------------------------------------------

    def _seasonality(self, d: date) -> float:
        """Weekly + annual seasonality. B2B dips at weekends and in late December."""
        weekly = 1.0 - 0.42 * (d.weekday() >= 5)
        annual = 1.0 + 0.13 * math.sin(2 * math.pi * (d.timetuple().tm_yday - 80) / 365.0)
        holiday = 0.55 if (d.month == 12 and d.day >= 22) else 1.0
        return weekly * annual * holiday

    def _growth(self, d: date) -> float:
        """Baseline compound growth, ~1.0% per month."""
        months = (d - self.start).days / 30.44
        return 1.010 ** months

    def _support_pressure(self, region: str, d: date) -> float:
        """Returns a multiplier >1 when the regression is active in this region."""
        if region != self.support_regression_region or d < self.support_regression_start:
            return 1.0
        weeks = (d - self.support_regression_start).days / 7.0
        return 1.0 + min(2.10, 0.30 * weeks)  # ramps then plateaus

    def _churn_hazard(self, segment: str, region: str, d: date) -> float:
        base = {"Enterprise": 0.0009, "Mid-Market": 0.0018,
                "SMB": 0.0031, "Self-Serve": 0.0062}[segment]
        # Churn responds to support pressure with a 3-6 week lag.
        lagged = d - timedelta(days=int(self.rng.integers(21, 45)))
        pressure = self._support_pressure(region, lagged)
        multiplier = 1.0 + (pressure - 1.0) * (9.5 if segment == "Enterprise" else 1.6)
        return base * multiplier

    # -- entity generation ----------------------------------------------------

    def customers(self) -> pd.DataFrame:
        n = self.n_customers
        signup_offsets = self.rng.integers(0, len(self.dates) - 30, n)
        # Older cohorts skew larger; sample segment with mild dependence on tenure.
        segments = self.rng.choice(SEGMENTS, n, p=SEGMENT_WEIGHT)
        regions = self.rng.choice(REGIONS, n, p=REGION_WEIGHT)
        df = pd.DataFrame({
            "customer_id": [f"CUS-{i:06d}" for i in range(1, n + 1)],
            "company_name": [f"{self._word()} {self._suffix()}" for _ in range(n)],
            "segment": segments,
            "region": regions,
            "channel": self.rng.choice(CHANNELS, n, p=[0.24, 0.19, 0.41, 0.16]),
            "signup_date": [self.dates[o].date() for o in signup_offsets],
            "employee_count": np.clip(
                self.rng.lognormal(4.2, 1.35, n).astype(int), 1, 90_000),
            "annual_contract_value": 0.0,
            "churn_date": pd.NaT,
            "is_active": True,
        })

        acv = []
        for seg in df["segment"]:
            mu, sigma = SEGMENT_ORDER_VALUE[seg]
            acv.append(max(120.0, self.rng.normal(mu * 4.5, sigma * 3.0)))
        df["annual_contract_value"] = np.round(acv, 2)

        # Simulate churn day by day so the hazard can respond to support pressure.
        churn_dates: list = [pd.NaT] * n
        for idx in range(n):
            seg, reg = df.at[idx, "segment"], df.at[idx, "region"]
            cur = df.at[idx, "signup_date"] + timedelta(days=int(self.rng.integers(30, 120)))
            while cur <= self.end:
                if self.rng.random() < self._churn_hazard(seg, reg, cur):
                    churn_dates[idx] = pd.Timestamp(cur)
                    break
                cur += timedelta(days=7)
        df["churn_date"] = churn_dates
        df["is_active"] = df["churn_date"].isna()
        return df

    def products(self) -> pd.DataFrame:
        rows = []
        pid = 1
        for cat in CATEGORIES:
            for i in range(self.rng.integers(5, 10)):
                base = float(np.round(self.rng.lognormal(5.6, 0.75), 2))
                rows.append({
                    "product_id": f"PRD-{pid:04d}",
                    "product_name": f"{cat.split()[0]} {self._word()} {['Core','Plus','Pro','Max','Lite'][i % 5]}",
                    "category": cat,
                    "list_price": base,
                    "unit_cost": round(base * float(self.rng.uniform(0.28, 0.58)), 2),
                    "launch_date": self.dates[int(self.rng.integers(0, 200))].date(),
                })
                pid += 1
        return pd.DataFrame(rows)

    def orders(self, customers: pd.DataFrame, products: pd.DataFrame):
        order_rows, item_rows, refund_rows = [], [], []
        oid = 1
        prod_by_cat = {c: products[products.category == c] for c in CATEGORIES}

        cust = customers.set_index("customer_id")
        for cid, row in cust.iterrows():
            seg, reg = row["segment"], row["region"]
            rate = SEGMENT_ORDER_RATE[seg]
            start = max(row["signup_date"], self.start)
            end = row["churn_date"].date() if pd.notna(row["churn_date"]) else self.end
            if end <= start:
                continue

            for d in pd.date_range(start, end, freq="D"):
                dd = d.date()
                # Accounts under support pressure contract their spend for weeks
                # before they formally churn -- this is what makes the decline
                # visible in revenue before it is visible in the churn table.
                contraction = 1.0 / (1.0 + 1.35 * (self._support_pressure(reg, dd) - 1.0)
                                     * (1.6 if seg == "Enterprise" else 0.18))
                lam = rate * self._seasonality(dd) * self._growth(dd) * contraction / 30.44
                if self.rng.random() > lam:
                    continue

                cat = self.rng.choice(CATEGORIES, p=[0.34, 0.22, 0.18, 0.16, 0.10])
                pool = prod_by_cat[cat]
                n_items = int(self.rng.integers(1, 5))
                order_total, order_disc = 0.0, 0.0
                items_for_order = []

                for _ in range(n_items):
                    p = pool.iloc[int(self.rng.integers(0, len(pool)))]
                    qty = int(max(1, self.rng.poisson(2 if seg != "Enterprise" else 6)))
                    unit = float(p["list_price"])

                    # Competitor price pressure forces deeper discounting.
                    disc = float(self.rng.beta(2, 12)) * 0.6
                    if (cat == self.price_pressure_category and dd >= self.price_pressure_start):
                        disc += float(self.rng.uniform(0.06, 0.16))
                    disc = min(disc, 0.45)

                    line = round(unit * qty * (1 - disc), 2)
                    items_for_order.append({
                        "order_id": f"ORD-{oid:07d}",
                        "product_id": p["product_id"],
                        "quantity": qty,
                        "unit_price": unit,
                        "discount_pct": round(disc, 4),
                        "line_total": line,
                        "unit_cost": float(p["unit_cost"]),
                    })
                    order_total += line
                    order_disc += unit * qty * disc

                status = self.rng.choice(
                    ["completed", "completed", "completed", "completed", "cancelled"],
                    p=[0.25, 0.25, 0.25, 0.22, 0.03])
                order_rows.append({
                    "order_id": f"ORD-{oid:07d}",
                    "customer_id": cid,
                    "order_date": dd,
                    "region": reg,
                    "segment": seg,
                    "channel": row["channel"],
                    "category": cat,
                    "status": status,
                    "total_amount": round(order_total, 2),
                    "discount_amount": round(order_disc, 2),
                    "item_count": n_items,
                })
                item_rows.extend(items_for_order)

                if status == "completed" and self.rng.random() < 0.021:
                    refund_rows.append({
                        "refund_id": f"REF-{len(refund_rows) + 1:06d}",
                        "order_id": f"ORD-{oid:07d}",
                        "customer_id": cid,
                        "refund_date": dd + timedelta(days=int(self.rng.integers(2, 40))),
                        "amount": round(order_total * float(self.rng.uniform(0.2, 1.0)), 2),
                        "reason": str(self.rng.choice(
                            ["defective", "not_as_described", "late_delivery",
                             "changed_mind", "billing_error"],
                            p=[0.18, 0.22, 0.24, 0.26, 0.10])),
                    })
                oid += 1

        refunds = pd.DataFrame(refund_rows)
        if not refunds.empty:
            refunds = refunds[refunds["refund_date"] <= self.end]
        return pd.DataFrame(order_rows), pd.DataFrame(item_rows), refunds

    def support_tickets(self, customers: pd.DataFrame) -> pd.DataFrame:
        rows = []
        tid = 1
        cust = customers.set_index("customer_id")
        for cid, row in cust.iterrows():
            reg, seg = row["region"], row["segment"]
            base = {"Enterprise": 0.055, "Mid-Market": 0.032,
                    "SMB": 0.018, "Self-Serve": 0.009}[seg]
            start = max(row["signup_date"], self.start)
            end = row["churn_date"].date() if pd.notna(row["churn_date"]) else self.end
            if end <= start:
                continue
            for d in pd.date_range(start, end, freq="D"):
                dd = d.date()
                pressure = self._support_pressure(reg, dd)
                if self.rng.random() > base * pressure * self._seasonality(dd):
                    continue
                # First-response time degrades under the same regression.
                frt = float(self.rng.gamma(2.2, 1.9)) * pressure
                rows.append({
                    "ticket_id": f"TIC-{tid:07d}",
                    "customer_id": cid,
                    "created_date": dd,
                    "region": reg,
                    "segment": seg,
                    "priority": str(self.rng.choice(["low", "medium", "high", "urgent"],
                                                    p=[0.34, 0.38, 0.21, 0.07])),
                    "category": str(self.rng.choice(
                        ["billing", "bug", "how_to", "outage", "feature_request"],
                        p=[0.18, 0.31, 0.26, 0.09, 0.16])),
                    "first_response_hours": round(frt, 2),
                    "resolution_hours": round(frt * float(self.rng.uniform(2.0, 9.0)), 2),
                    "csat_score": int(np.clip(
                        round(self.rng.normal(4.35 - 0.55 * (pressure - 1), 0.85)), 1, 5)),
                })
                tid += 1
        return pd.DataFrame(rows)

    def marketing_campaigns(self) -> pd.DataFrame:
        rows = []
        d = self.start
        cid = 1
        while d < self.end:
            length = int(self.rng.integers(14, 60))
            spend = float(np.round(self.rng.lognormal(9.6, 0.6), 2))
            rows.append({
                "campaign_id": f"CMP-{cid:04d}",
                "campaign_name": f"{self._word()} {self.rng.choice(['Launch','Push','Wave','Drive'])}",
                "channel": str(self.rng.choice(
                    ["Paid Search", "Paid Social", "Email", "Events", "Content"])),
                "region": str(self.rng.choice(REGIONS, p=REGION_WEIGHT)),
                "start_date": d, "end_date": d + timedelta(days=length),
                "budget": spend,
                "spend": round(spend * float(self.rng.uniform(0.72, 1.04)), 2),
                "impressions": int(spend * self.rng.uniform(80, 260)),
                "clicks": int(spend * self.rng.uniform(1.4, 5.5)),
                "leads": int(spend * self.rng.uniform(0.03, 0.14)),
            })
            d += timedelta(days=int(self.rng.integers(7, 22)))
            cid += 1
        return pd.DataFrame(rows)

    def inventory(self, products: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for _, p in products.iterrows():
            for reg in REGIONS:
                on_hand = int(self.rng.integers(0, 1400))
                reorder = int(self.rng.integers(80, 400))
                rows.append({
                    "product_id": p["product_id"], "region": reg,
                    "units_on_hand": on_hand, "reorder_point": reorder,
                    "lead_time_days": int(self.rng.integers(3, 45)),
                    "units_on_order": int(self.rng.integers(0, 600)),
                    "last_counted": self.end - timedelta(days=int(self.rng.integers(1, 90))),
                    "stockout_risk": round(min(1.0, max(0.0, (reorder - on_hand) / max(reorder, 1))), 3),
                })
        return pd.DataFrame(rows)

    def subscriptions(self, customers: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for cid, row in customers.set_index("customer_id").iterrows():
            term = int(self.rng.choice([12, 24, 36], p=[0.55, 0.32, 0.13]))
            start = row["signup_date"]
            rows.append({
                "subscription_id": f"SUB-{len(rows) + 1:06d}",
                "customer_id": cid,
                "plan": str(self.rng.choice(["Starter", "Growth", "Business", "Enterprise"],
                                            p=[0.34, 0.31, 0.23, 0.12])),
                "mrr": round(row["annual_contract_value"] / 12.0, 2),
                "term_months": term,
                "start_date": start,
                "renewal_date": start + timedelta(days=term * 30),
                "status": "churned" if pd.notna(row["churn_date"]) else "active",
                "cancelled_date": row["churn_date"],
            })
        return pd.DataFrame(rows)

    # -- naming ---------------------------------------------------------------
    _WORDS = ["Northwind", "Cobalt", "Trellis", "Ironwood", "Halcyon", "Meridian",
              "Lumen", "Basalt", "Verdant", "Cinder", "Auric", "Quarry", "Foxglove",
              "Beacon", "Alder", "Sable", "Pinnacle", "Vantage", "Kestrel", "Thorne"]
    _SUFFIX = ["Industries", "Systems", "Group", "Holdings", "Labs", "Partners",
               "Logistics", "Technologies", "Works", "Collective"]

    def _word(self) -> str:
        return str(self.rng.choice(self._WORDS))

    def _suffix(self) -> str:
        return str(self.rng.choice(self._SUFFIX))


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the InsightOS demo business dataset")
    ap.add_argument("--out", default="./seed", help="output directory")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--customers", type=int, default=4200)
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--format", choices=["csv", "parquet"], default="csv")
    ap.add_argument("--incident-start", default="2025-06-16",
                    help="date the latent support-quality regression begins")
    ap.add_argument("--postgres", default="", help="optional SQLAlchemy URL to load into")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sim = BusinessSimulator(
        seed=args.seed, months=args.months, n_customers=args.customers,
        incident_start=date.fromisoformat(args.incident_start),
    )
    print("Generating customers...")
    customers = sim.customers()
    print("Generating products...")
    products = sim.products()
    print("Generating orders (this is the slow one)...")
    orders, order_items, refunds = sim.orders(customers, products)
    print("Generating support tickets...")
    tickets = sim.support_tickets(customers)
    print("Generating campaigns, inventory, subscriptions...")
    campaigns = sim.marketing_campaigns()
    inv = sim.inventory(products)
    subs = sim.subscriptions(customers)

    tables = {
        "customers": customers, "products": products, "orders": orders,
        "order_items": order_items, "refunds": refunds, "support_tickets": tickets,
        "marketing_campaigns": campaigns, "inventory": inv, "subscriptions": subs,
    }

    for name, df in tables.items():
        path = out / f"{name}.{args.format}"
        if args.format == "csv":
            df.to_csv(path, index=False)
        else:
            df.to_parquet(path, index=False)
        print(f"  {name:22s} {len(df):>9,} rows -> {path}")

    if args.postgres:
        from sqlalchemy import create_engine
        engine = create_engine(args.postgres)
        for name, df in tables.items():
            df.to_sql(name, engine, if_exists="replace", index=False, chunksize=5000)
            print(f"  loaded {name} into Postgres")

    print(f"\nDone. {sum(len(d) for d in tables.values()):,} rows total.")


if __name__ == "__main__":
    main()
