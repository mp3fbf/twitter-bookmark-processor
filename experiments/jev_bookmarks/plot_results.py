"""Render paired pilot results as standalone figures, without external services."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .pilot import load_sample, paired_metrics, priority_level, read_lines


def paired_rows(data, split):
    rows, _ = load_sample(data)
    results = {}
    for provider in ("baseline", "jev"):
        latest = {r["id"]: r for r in read_lines(data / f"{provider}.jsonl")}
        results[provider] = {
            key: record for key, record in latest.items() if record["status"] == "succeeded"
        }
    selected = [r for r in rows if r["split"] == split]
    if any(r["id"] not in results[p] for r in selected for p in results):
        raise ValueError("Render only a completed paired split.")
    pairs = [(r["id"], results["baseline"][r["id"]], results["jev"][r["id"]]) for r in selected]
    return selected, pairs


def draw(axes, pairs, metrics):
    blue, grey = "#21618c", "#6b7280"
    latency, agreement = axes
    for index, color, label, ypos in [(1, grey, "Luna", 0.20), (2, blue, "Jev", 0.40)]:
        times = sorted(p[index]["elapsed_seconds"] for p in pairs)
        fractions = [(i + 1) / len(times) for i in range(len(times))]
        latency.step([times[0], *times], [0, *fractions], where="post", color=color, linewidth=2)
        median = metrics["median_baseline_seconds" if index == 1 else "median_jev_seconds"]
        latency.annotate(
            f"{label}\nmediana {median:.2f} s".replace(".", ","),
            xy=(median, 0.5),
            xytext=(median * (1.5 if index == 2 else 0.65), ypos),
            ha="left" if index == 2 else "right",
            color=color,
            fontsize=11,
            arrowprops={"arrowstyle": "-", "color": color, "linewidth": 0.8},
        )
    max_time = max(p[i]["elapsed_seconds"] for p in pairs for i in (1, 2))
    latency.set_xscale("log")
    latency.set_xlim(0.4, max_time * 1.8)
    latency.set_ylim(0, 1.04)
    latency.set_yticks([0, 0.5, 1], ["0%", "50%", "100%"])
    ticks = [t for t in [0.5, 1, 2, 5, 10, 20, 50, 100] if t <= max_time * 1.7]
    latency.set_xticks(ticks, [str(t).replace(".", ",") for t in ticks])
    latency.minorticks_off()
    latency.set_xlabel("Segundos por item · escala logarítmica", fontsize=10)
    latency.set_ylabel("Itens concluídos", fontsize=10)
    latency.set_title("Jev respondeu mais rápido", loc="left", fontsize=13, pad=14)
    latency.grid(axis="y", color="#e5e7eb", linewidth=0.7)

    values = [
        metrics["topic_agreement"],
        metrics["modal_priority_agreement"],
        metrics["needs_context_agreement"],
    ]
    labels = ["Mesmo tema", "Mesmo nível\nde prioridade", "Mesma necessidade\nde contexto"]
    for y, value in zip([2, 1, 0], values):
        agreement.hlines(y, 0, 1, color="#e5e7eb", linewidth=2)
        agreement.hlines(y, 0, value, color=blue, linewidth=2)
        agreement.plot(value, y, "o", color=blue, markersize=6)
        agreement.text(value + 0.025, y, f"{value:.0%}", va="center", fontsize=11, color=blue)
    agreement.set_xlim(0, 1.16)
    agreement.set_ylim(-0.65, 2.65)
    agreement.set_yticks([2, 1, 0], labels, fontsize=10)
    agreement.set_xticks([0, 0.5, 1], ["0%", "50%", "100%"])
    agreement.set_title("Onde as decisões coincidem?", loc="left", fontsize=13, pad=14)
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.spines[["bottom", "left"]].set_color("#d1d5db")
        axis.tick_params(length=0, pad=6, labelsize=10)


def render(data, out, split):
    rows, pairs = paired_rows(data, split)
    metrics = paired_metrics(pairs)
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {"font.family": "DejaVu Sans", "axes.labelcolor": "#374151", "text.color": "#1f2937"}
    )
    phase = "Avaliação final reservada" if split == "holdout" else "Prévia da calibração"
    for mobile in (False, True):
        if mobile:
            figure, axes = plt.subplots(2, 1, figsize=(5.8, 10.4))
            figure.subplots_adjust(left=0.28, right=0.92, top=0.81, bottom=0.17, hspace=0.85)
        else:
            figure, axes = plt.subplots(1, 2, figsize=(12, 5.6))
            figure.subplots_adjust(left=0.07, right=0.97, top=0.74, bottom=0.30, wspace=0.65)
        draw(axes, pairs, metrics)
        title = (
            "Jev × Luna\nVelocidade e concordância"
            if mobile
            else "Jev × Luna: velocidade e concordância"
        )
        figure.suptitle(title, x=0.06, y=0.98, ha="left", fontsize=16, fontweight="bold")
        figure.text(
            0.06,
            0.90 if not mobile else 0.91,
            f"{phase} · {len(pairs)} bookmarks pareados",
            fontsize=11,
        )
        figure.text(
            0.06,
            0.06,
            "Concordância com outro modelo não mede acurácia humana.\n"
            "Tempo inclui rede; Luna também inclui inicialização do CLI.\n"
            "Mesmos textos e critérios; prioridade comparada pelo nível mais provável.",
            fontsize=9,
            color="#4b5563",
        )
        name = "comparacao-mobile" if mobile else "comparacao"
        figure.savefig(out / f"{name}.png", dpi=180, facecolor="white")
        figure.savefig(out / f"{name}.svg", facecolor="white")
        plt.close(figure)
    with (out / "itens-pareados.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "id",
                "url",
                "topic_baseline",
                "topic_jev",
                "priority_baseline",
                "priority_jev_modal",
                "priority_jev_mean",
                "context_baseline",
                "context_jev",
                "latency_baseline_s",
                "latency_jev_s",
                "jev_cost_usd",
            ]
        )
        lookup = {r["id"]: r for r in rows}
        for key, baseline, jev in pairs:
            a, b = baseline["prediction"], jev["prediction"]
            writer.writerow(
                [
                    key,
                    lookup[key]["url"],
                    a["topic"],
                    b["topic"],
                    priority_level(baseline),
                    priority_level(jev),
                    b["priority"],
                    a["needs_context"],
                    b["needs_context"],
                    baseline["elapsed_seconds"],
                    jev["elapsed_seconds"],
                    jev["cost_usd"],
                ]
            )
    return metrics


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--split", choices=["calibration", "holdout"], default="holdout")
    args = parser.parse_args()
    render(args.data, args.out, args.split)
