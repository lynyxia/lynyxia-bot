"""Pure emote-set comparison and HTML rendering (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.reporting import emote_image_url, emote_link, escape, report_shell

STYLE = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, sans-serif;
    background: #18181b;
    color: #e4e4e7;
    padding: 2rem;
    line-height: 1.5;
  }
  h1 { text-align: center; font-size: 2rem; margin-bottom: 0.5rem; color: #a259ff; }
  .subtitle { text-align: center; color: #71717a; margin-bottom: 2rem; font-size: 0.95rem; }
  .summary {
    display: flex; justify-content: center; gap: 2rem; margin-bottom: 2rem; flex-wrap: wrap;
  }
  .summary-card {
    background: #27272a; border-radius: 8px; padding: 1rem 1.5rem;
    text-align: center; min-width: 160px;
  }
  .summary-card h3 { font-size: 1.8rem; color: #a259ff; }
  .summary-card p { color: #a1a1aa; font-size: 0.85rem; }
  table {
    width: 100%; border-collapse: collapse; margin-bottom: 2rem;
    background: #27272a; border-radius: 8px; overflow: hidden;
  }
  th, td { padding: 0.65rem 1rem; text-align: left; border-bottom: 1px solid #3f3f46; }
  th {
    background: #3f3f46; color: #fafafa; font-weight: 600; font-size: 0.8rem;
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #2e2e33; }
  .emote-cell { display: flex; align-items: center; gap: 0.75rem; }
  .emote-img {
    max-height: 48px; border-radius: 6px; flex-shrink: 0; background: #18181b;
  }
  .emote-name {
    color: #d2a8ff; text-decoration: none; font-weight: 500; white-space: nowrap;
  }
  .emote-name:hover { text-decoration: underline; color: #a259ff; }
  .missing-badge {
    display: inline-block; background: #f97316; border-radius: 4px;
    padding: 0.2rem 0.6rem; font-size: 0.75rem; color: #18181b;
    font-weight: 700; margin-right: 0.5rem;
  }
  .section-title {
    font-size: 1.3rem; color: #e4e4e7; margin: 2rem 0 1rem;
    padding-bottom: 0.5rem; border-bottom: 2px solid #3f3f46;
  }
  .section-title span { color: #a259ff; }
  .no-missing {
    color: #4ade80; font-weight: 600; padding: 1rem; text-align: center;
    background: #27272a; border-radius: 8px;
  }
  .side-by-side { display: flex; gap: 1.5rem; margin-bottom: 2rem; }
  .side-col { flex: 1; min-width: 0; }
  .side-col h3 {
    font-size: 1rem; color: #d2a8ff; margin-bottom: 0.5rem;
    padding-bottom: 0.4rem; border-bottom: 1px solid #3f3f46;
  }
  .meta { text-align: center; color: #71717a; font-size: 0.85rem; margin-bottom: 1.5rem; }
  footer {
    text-align: center; margin-top: 3rem; padding-top: 1.5rem;
    border-top: 1px solid #3f3f46; color: #71717a; font-size: 0.85rem;
  }
"""


@dataclass(frozen=True)
class EmoteSet:
    id: str
    name: str
    emotes: list[dict]


@dataclass(frozen=True)
class Comparison:
    set_a: EmoteSet
    set_b: EmoteSet
    # (name, emote data) pairs, sorted by name.
    missing_from_b: list[tuple[str, dict]]
    missing_from_a: list[tuple[str, dict]]


def build_index(emotes: list[dict]) -> dict[str, dict]:
    """Index emotes by name, as the attached comparison script does."""
    return {name: emote for emote in emotes if (name := str(emote.get("name") or ""))}


def build_comparisons(sets: list[EmoteSet]) -> list[Comparison]:
    """Compare every pair of sets, finding emote names each one lacks."""
    indexes = [build_index(item.emotes) for item in sets]
    comparisons: list[Comparison] = []
    for i, first in enumerate(sets):
        for j in range(i + 1, len(sets)):
            second = sets[j]
            names_a, names_b = set(indexes[i]), set(indexes[j])
            missing_from_b = sorted(names_a - names_b)
            missing_from_a = sorted(names_b - names_a)
            comparisons.append(
                Comparison(
                    set_a=first,
                    set_b=second,
                    missing_from_b=[(name, indexes[i][name]) for name in missing_from_b],
                    missing_from_a=[(name, indexes[j][name]) for name in missing_from_a],
                )
            )
    return comparisons


def _emote_field(emote: dict, key: str) -> object:
    if key in emote:
        return emote[key]
    data = emote.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return None


def emote_details(emote: dict) -> str:
    """Short human-readable description: animated/tag count, or Static."""
    details: list[str] = []
    if _emote_field(emote, "animated"):
        details.append("Animated")
    tags = _emote_field(emote, "tags")
    if isinstance(tags, list) and tags:
        noun = "tag" if len(tags) == 1 else "tags"
        details.append(f"{len(tags)} {noun}")
    return ", ".join(details) or "Static"


def _render_column(entries: list[tuple[str, dict]], other_name: str) -> str:
    parts = [f'<div class="side-col">\n<h3>Missing from {escape(other_name)}</h3>\n']
    if not entries:
        parts.append('<p class="no-missing">&#10004; All emotes match!</p>\n')
    else:
        parts.append(
            "<table>\n<tr><th>#</th><th>Emote</th><th>Details</th></tr>\n"
        )
        for index, (name, emote) in enumerate(entries, 1):
            image_url = emote_image_url(emote)
            image = (
                f'<img class="emote-img" src="{escape(image_url)}" '
                f'alt="{escape(name)}" loading="lazy">'
                if image_url
                else ""
            )
            parts.append(
                f"<tr><td>{index}</td>"
                f'<td><div class="emote-cell">{image}'
                f'<a class="emote-name" href="{escape(emote_link(emote))}" '
                f'target="_blank">{escape(name)}</a></div></td>'
                f"<td>{escape(emote_details(emote))}</td></tr>\n"
            )
        parts.append("</table>\n")
    parts.append("</div>\n")
    return "".join(parts)


def render_report(
    comparisons: list[Comparison],
    sets: list[EmoteSet],
    channel: str,
    truncated: bool,
) -> str:
    """Build the full HTML comparison report with escaped dynamic values."""
    body: list[str] = ['<div class="summary">\n']
    for item in sets:
        body.append(
            f'<div class="summary-card"><h3>{len(build_index(item.emotes))}</h3>'
            f"<p>{escape(item.name)}</p></div>\n"
        )
    body.append("</div>\n\n")
    if truncated:
        body.append(
            '<p class="meta">Only the first few emote sets (alphabetically) '
            "were compared.</p>\n"
        )

    for comparison in comparisons:
        body.append(
            f'<div class="section-title">{escape(comparison.set_a.name)} '
            f"<span>vs</span> {escape(comparison.set_b.name)}</div>\n"
        )
        body.append('<div class="side-by-side">\n')
        body.append(
            _render_column(comparison.missing_from_b, comparison.set_b.name)
        )
        body.append(
            _render_column(comparison.missing_from_a, comparison.set_a.name)
        )
        body.append("</div>\n")

    return report_shell(
        "7TV Emote Set Comparison",
        f"{channel} — Cross-comparison Report",
        STYLE,
        "".join(body),
    )
