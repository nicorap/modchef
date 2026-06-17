"""Toolchain compatibility hierarchy.

`resolve_chain` requires EasyBuild (build-time) and is generation-aware.
`ancestors`/`compatible` operate on a plain {tc_id: {sub_tc_ids}} dict so they
are pure and testable, and are the only functions used at runtime.
"""

_CONFIGURED = False
_CHAIN_CACHE = {}


def resolve_chain(tc_name, tc_version):
    """Base-first list of tc_id strings for a toolchain, e.g.
    ('gfbf','2023a') -> ['GCCcore-12.3.0', 'GCC-12.3.0', 'gfbf-2023a'].
    Returns [f"{tc_name}-{tc_version}"] if EasyBuild cannot resolve it."""
    global _CONFIGURED
    key = (tc_name, tc_version)
    if key in _CHAIN_CACHE:
        return _CHAIN_CACHE[key]
    if not _CONFIGURED:
        from easybuild.tools.options import set_up_configuration
        set_up_configuration(args=[], silent=True)
        _CONFIGURED = True
    from easybuild.framework.easyconfig.easyconfig import get_toolchain_hierarchy
    try:
        chain = [f"{t['name']}-{t['version']}"
                 for t in get_toolchain_hierarchy(
                     {"name": tc_name, "version": tc_version})]
    except Exception:
        chain = [f"{tc_name}-{tc_version}"]
    _CHAIN_CACHE[key] = chain
    return chain


def ancestors(tc_id, hierarchy):
    """All sub-toolchain tc_ids reachable downward, including `tc_id`."""
    seen = set()
    stack = [tc_id]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(hierarchy.get(cur, ()))
    return seen


def compatible(a, b, hierarchy):
    """True if a and b lie on one chain (one is an ancestor of the other)."""
    return b in ancestors(a, hierarchy) or a in ancestors(b, hierarchy)
