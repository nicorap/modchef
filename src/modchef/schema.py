"""RDF vocabulary, URI builders, and naming helpers for modchef."""
from rdflib import Namespace

MC = Namespace("https://modchef.dev/schema#")
RES = Namespace("https://modchef.dev/r/")

SYSTEM_TOOLCHAINS = {None, "", "system"}


def slug(value) -> str:
    """Lowercase, replace non-alphanumerics with hyphens, trim hyphens."""
    s = "".join(c if c.isalnum() else "-" for c in str(value).lower())
    return "-".join(part for part in s.split("-") if part)


def toolchain_id(tc_name, tc_version):
    """Return 'name-version' or None for the system toolchain."""
    if tc_name in SYSTEM_TOOLCHAINS:
        return None
    return f"{tc_name}-{tc_version}"


def full_module_name(name, version, tc_name, tc_version, versionsuffix="") -> str:
    """Reconstruct the EasyBuild module name, e.g. SAMtools/1.22-GCC-14.3.0."""
    tc = toolchain_id(tc_name, tc_version)
    suffix = versionsuffix or ""
    tc_part = f"-{tc}" if tc else ""
    return f"{name}/{version}{tc_part}{suffix}"


def module_uri(full_name):
    return RES["m-" + slug(full_name)]


def software_uri(name):
    return RES["sw-" + slug(name)]


def package_uri(name, ecosystem):
    return RES[f"pkg-{ecosystem}-" + slug(name)]


def toolchain_uri(tc_id):
    return RES["tc-" + slug(tc_id)]
