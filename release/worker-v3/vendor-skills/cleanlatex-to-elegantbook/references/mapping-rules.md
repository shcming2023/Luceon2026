# CleanLaTeX -> ElegantBook Mapping Rules

## Scope

This skill starts from CleanLaTeX, not raw OCR. It assumes the text has already been cleaned enough that section titles, item labels, math, tables, and images are meaningful. The conversion is a semantic styling pass.

For formal frozen-plan production, the current Spec 04-D contract overrides the historical heuristic table below. In particular, `response_list` is legal only when the exact source items, display labels, column count, and answer-space parameters were frozen upstream; the renderer never discovers or changes them from LaTeX text.

## Core Mapping

| CleanLaTeX Pattern | ElegantBook Output |
| --- | --- |
| `\section*{Article or unit title}` | `\chapter{Article or unit title}` |
| `\subsection*{General heading}` | `\section{General heading}` |
| `\subsubsection*{General subheading}` | `\subsection{General subheading}` |
| Metadata labels such as `语篇类型`, `词数`, `难度`, `范畴`, `教材链接` | `infobox` containing an aligned `description` list whose labels remain inside the frame |
| `\subsection*{Word power}` | `vocabbox` containing the following content until the next heading |
| `\subsection*{Language tips}` | `tipbox` containing the following content until the next heading |
| `\subsection*{Quiz}`, `Quiz-`, roman exercise headings, `Task 1:` | `\exerciseheading{...}` followed by the original exercise body |
| `longtable`, `tabular`, formulas, images | Preserve directly; do not force inside semantic boxes |

## English Reading Materials

For reading-training books or newspapers:

- Treat each reading passage title as a chapter.
- Keep passage metadata near the chapter opening in `infobox`.
- Merge split metadata boxes at the chapter opening. A passage should not show three repeated `Reading profile` boxes for `语篇类型/词数/难度`, `范畴`, and `教材链接`.
- Normalize `范畴` to the visible profile label `范围`, and keep `范围` plus `教材链接` inside the same framed profile instead of leaving them as loose paragraphs below it.
- Never place a figure between two fragments of one sentence. Move the figure to the preceding paragraph boundary, rejoin the sentence, and preserve source order.
- Render a simple source word bank as one bordered row when the source visibly uses a word-bank box.
- Keep QR codes small and compact near the opening metadata; do not let one-size-fits-all image widths enlarge QR codes into page-dominant figures.
- Keep article prose unboxed.
- Use `vocabbox` for glossary/word-power material.
- Use `tipbox` for grammar or sentence-pattern notes.
- End `tipbox` before quiz questions. If a `Language tips` block contains a later `enumerate`, `Task`, or translation exercise heading, the exercise material must be moved outside the box.
- Use `exerciseheading` for quizzes, tasks, and question groups so long tables and enumerations compile normally.
- Standalone roman headings written as plain text, such as `I. Complete...`, should become `exerciseheading`. A singleton enumerate item that only says `Translate the sentences according to the Chinese.` should also become the appropriate roman exercise heading based on its counter.
- Obvious OCR metadata variants such as `词段` or `词置` should normalize to `词数`; do not make broader content edits without source evidence.

## Media and Covers

- Preserve source image filenames and paths. The converter may change sizing options, but it must not replace real image hashes with placeholders in final output.
- If the source images are present, classify by dimensions instead of file name:
  - QR/small square images: about `0.18\textwidth`.
  - Small icons or seals: about `0.30\textwidth`.
  - Normal article photos/illustrations: about `0.48\textwidth`.
  - Wide images/tables-as-images: about `0.78\textwidth`.
  - Tall images: constrain by height.
- Covers must be explicit. Prefer a source-derived cover image when the original PDF or image evidence is available. Decorative cover art or unrelated logos are allowed only when the user explicitly requests them.

## Tables

- Do not leave long text tables as `l|l|l`; use `p{...}` columns with `\raggedright\arraybackslash` so Chinese and English text wraps inside the page.
- Very wide timetable-style tables may be scaled with `\resizebox{\textwidth}{!}{...}` after confirming they fit on one page.
- After modifying a table, render the page; compile success alone is not enough because overfull tables can silently clip content.

## Chinese Textbook Materials

For Chinese math or teaching-reference materials:

- Chapters and lessons become `\chapter` / `\section`.
- Stable pedagogical columns such as `知识点`, `教材例题`, `例题讲解`, `归纳总结`, `误区警示`, `重点提示` may be mapped to tcolorbox styles when a project-specific style is desired.
- Do not restyle every short label as a box. Use boxes for repeated semantic columns that benefit from visual scanning.

## Compile Rules

- Use XeLaTeX.
- `main.tex` must remain an ElegantBook entry point.
- If local TeX lacks `elegantbook.cls`, generate and compile `main-fallback.tex` with `ctexbook` for validation only.
- Use `--demo-images` when image assets are unavailable locally; remove it for final Overleaf packaging when the images folder is complete.

## Review Rules

- The conversion must be reversible at the content level: no paragraphs, questions, tables, or figures should disappear.
- Styling should clarify semantics without hiding long exercises in fragile environments.
- Missing image files are warnings, not reasons to rewrite image references.
- Chapter numbering should begin with the first real learning unit. Source front matter, prefaces, usage instructions, and original contents belong in front matter or unnumbered sections.
