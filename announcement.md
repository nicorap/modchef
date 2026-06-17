2026-06-17
## modchef 1.0.0 is here!  `ml modchef`

  **What's new:**
  - Describe what you need — `modchef cook --tools samtools --python pandas --r ggplot2` — and modchef writes the `module load` recipe for you
  - Picks compatible versions and toolchains automatically, so the modules load together (with a `module purge` up front for a clean start)
  - Knows what's installed vs. only available in EasyBuild — and prints the exact module to request from support@ngc.dk when something's missing
  - `modchef search <name>` finds which module provides a tool or package — Python/R included — tagged `[installed]` / `[available]`

  Run `modchef --help` for all options, or see **Using Modules** on the wiki for the full guide.
