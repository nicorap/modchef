# modchef

Cook EasyBuild environments from software ingredients.

## Usage

    modchef cook --tools samtools bcftools multiqc --python pandas numpy
    modchef cook recipe.yaml --output load_modules.sh
    modchef search bwa
    modchef explain SAMtools/1.22-GCC-14.3.0
    modchef menu

## Daily index (cron, runs as the EasyBuild admin user)

`modchef-index` needs the EasyBuild framework importable, so the cron loads the
EasyBuild module first (the runtime `modchef` module deliberately does *not*
depend on EasyBuild, so `cook`/`search` users stay lean):

    module load EasyBuild/5.2.0
    modchef-index --installed-root /opt/easybuild/software \
                  --robot-repo     /opt/easybuild/easyconfigs \
                  --official-repo  /opt/easybuild/easyconfigs \
                  --output /opt/easybuild/modchef/modchef.ttl

`--installed-root` is the EasyBuild software install tree: modchef indexes the
easyconfig EasyBuild stamped for each *actually installed* module
(`<name>/<version>/easybuild/*.eb`), so the catalog matches what `module avail`
shows, including every bundle's extensions. `--robot-repo` is an easyconfigs
collection used only to resolve installed dependencies. `--official-repo` is the
official EasyBuild collection, indexed as "available, not installed" with the
same full facts (deps + packages): `cook` builds from installed modules first
and, when a tool or package is only available, tells you the easyconfig to ask
support to install. Any easyconfig that fails to parse is reported on stderr
rather than silently dropped. Full-parsing the official collection makes the
daily index noticeably heavier.

The runtime CLI reads the graph from `$MODCHEF_TTL` (set by the module file).

## Deploying the module (EasyBuild, on the HPC as the EasyBuild admin user)

modchef ships as an EasyBuild module built from `easybuild/modchef-1.0.0-GCCcore-12.3.0.eb`
(toolchain `GCCcore/12.3.0`, so the installed module is `modchef/1.0.0-GCCcore-12.3.0`).

1. Build the source tarball from a checkout and copy it plus the easyconfig to the HPC:

        python -m build                       # writes dist/modchef-1.0.0.tar.gz
        scp dist/modchef-1.0.0.tar.gz                   <admin>@<hpc>:/opt/easybuild/ebfiles_repo/
        scp easybuild/modchef-1.0.0-GCCcore-12.3.0.eb   <admin>@<hpc>:/opt/easybuild/ebfiles_repo/

2. EasyBuild caches sources by filename, so overwrite any stale copy in its sourcepath
   (`eb --show-config | grep sourcepath`) before reinstalling:

        cp /opt/easybuild/ebfiles_repo/modchef-1.0.0.tar.gz <sourcepath>/m/modchef/

3. Reinstall over the existing module and smoke-test:

        eb /opt/easybuild/ebfiles_repo/modchef-1.0.0-GCCcore-12.3.0.eb --rebuild
        module load modchef/1.0.0-GCCcore-12.3.0
        modchef --help
        modchef-index --help | grep official-repo

The version stays `1.0.0` (rebuild in place), so use `--rebuild`. An earlier
SYSTEM-toolchain `modchef/1.0.0`, if any, is a separate module name — remove it
to avoid confusion.
