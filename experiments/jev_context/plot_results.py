"""Render the two predeclared context-sufficiency error counts."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .pilot import load, read_lines, report


def render(data, out):
    result = report(data)
    if not result["complete"]:
        raise ValueError("Only publish completed comparisons.")
    rows, _, _ = load(data)
    providers = [
        ("rules", "Regras", "#6b7280"),
        ("jev", "Jev", "#21618c"),
        ("baseline", "Luna", "#6b7280"),
    ]
    n = result["providers"]["jev"]["removed"]["eligible"]
    if n != result["providers"]["jev"]["sufficient"]["eligible"]:
        raise ValueError("Matched panels require equal denominators.")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    plt.rcParams.update(
        {"font.family": "DejaVu Sans", "text.color": "#1f2937", "axes.labelcolor": "#374151"}
    )
    panels = [
        ("removed", "false_ready_ids", "Aceitou texto\nsem a evidência"),
        ("sufficient", "unnecessary_retrieval_ids", "Pediu busca com\na resposta presente"),
    ]
    for mobile in (False, True):
        fig, axes = plt.subplots(
            2 if mobile else 1, 1 if mobile else 2, figsize=(6, 8.8) if mobile else (11, 5.4)
        )
        if mobile:
            fig.subplots_adjust(left=0.19, right=0.94, top=0.77, bottom=0.22, hspace=0.8)
        else:
            fig.subplots_adjust(left=0.10, right=0.96, top=0.70, bottom=0.27, wspace=0.45)
        for axis, (variant, field, title) in zip(axes, panels):
            for y, (provider, label, color) in zip([2, 1, 0], providers):
                count = len(result["providers"][provider][variant][field])
                axis.hlines(y, 0, count, color=color, linewidth=1.5)
                axis.plot(count, y, "o", color=color, markersize=7)
                axis.text(count + n * 0.035, y, str(count), va="center", fontsize=12, color=color)
            axis.set_xlim(-n * 0.02, n * 1.10)
            axis.set_ylim(-0.5, 2.5)
            axis.set_yticks([2, 1, 0], [p[1] for p in providers])
            axis.set_xticks([0, n / 2, n], ["0", str(n // 2), str(n)])
            axis.set_xlabel(f"Casos, de {n} · menor é melhor", fontsize=10)
            axis.set_title(title, fontsize=13, loc="left", pad=12)
            axis.grid(axis="x", linewidth=0.7, color="#e5e7eb")
            axis.set_axisbelow(True)
            axis.spines[["top", "right", "left"]].set_visible(False)
            axis.spines["bottom"].set_color("#d1d5db")
            axis.tick_params(length=0, labelsize=11, pad=6)
        title = (
            "Quem evita buscas\nsem aceitar lacunas?"
            if mobile
            else "Quem evita buscas sem aceitar lacunas?"
        )
        fig.suptitle(title, x=0.06, y=0.97, ha="left", fontsize=17, fontweight="bold")
        fig.text(
            0.06,
            0.86 if mobile else 0.87,
            f"{n} fontes · duas versões por fonte · rodada original",
            fontsize=10,
        )
        fig.text(
            0.06,
            0.055,
            "Referência: trechos selecionados pelo assistente\n"
            "e remoção controlada da informação solicitada.\n"
            "Não mede acurácia geral nem sucesso da busca.\n"
            "Perguntas reformuladas ficam em análise separada.",
            fontsize=9,
            color="#4b5563",
        )
        name = "contexto-mobile" if mobile else "contexto"
        fig.savefig(out / (name + ".png"), dpi=180, facecolor="white")
        fig.savefig(out / (name + ".svg"), facecolor="white")
        plt.close(fig)
    latest = {
        p: {r["id"]: r for r in read_lines(data / (p + ".jsonl")) if r["status"] == "succeeded"}
        for p, _, _ in providers
    }
    with (out / "casos.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "id",
                "source_url",
                "variant",
                "reading_goal",
                "acceptable_routes",
                "rules",
                "jev",
                "baseline",
                "jev_seconds",
                "baseline_seconds",
                "jev_cost_usd",
            ]
        )
        for row in rows:
            key = row["id"]
            writer.writerow(
                [
                    key,
                    row["state"]["post"]["url"],
                    row["variant"],
                    row["state"]["reading_goal"],
                    "|".join(row["acceptable_routes"]),
                    *[latest[p][key]["prediction"] for p, _, _ in providers],
                    latest["jev"][key]["elapsed_seconds"],
                    latest["baseline"][key]["elapsed_seconds"],
                    latest["jev"][key]["cost_usd"],
                ]
            )


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    render(args.data, args.out)
