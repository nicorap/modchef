"""Runtime query layer over the modchef RDF graph."""
from dataclasses import dataclass
from typing import Optional

from rdflib import Graph, Literal

from modchef import schema, toolchains


@dataclass(frozen=True)
class ModuleRef:
    uri: str
    full_name: str
    name: str
    version: str
    toolchain_id: Optional[str]
    installed: bool = True


class ModChefGraph:
    def __init__(self, graph: Graph):
        self.g = graph
        self._sub_hierarchy = self._load_hierarchy()

    @classmethod
    def load(cls, path):
        g = Graph()
        g.parse(path, format="turtle")
        return cls(g)

    def _load_hierarchy(self):
        """Build {tc_id: {direct sub tc_ids}} from mc:subToolchainOf edges."""
        hierarchy = {}
        for parent, _, child in self.g.triples(
                (None, schema.MC.subToolchainOf, None)):
            hierarchy.setdefault(self._tc_id(parent), set()).add(self._tc_id(child))
        return hierarchy

    def _tc_id(self, tc_uri):
        tc_id = self.g.value(tc_uri, schema.MC.toolchainId)
        if tc_id is not None:
            return str(tc_id)
        # fallback for nodes without an explicit id
        name = self.g.value(tc_uri, schema.MC.name)
        version = self.g.value(tc_uri, schema.MC.version)
        return f"{name}-{version}"

    def _module_ref(self, m_uri):
        tc_uri = self.g.value(m_uri, schema.MC.builtWith)
        inst = self.g.value(m_uri, schema.MC.installed)
        return ModuleRef(
            uri=str(m_uri),
            full_name=str(self.g.value(m_uri, schema.MC.fullName)),
            name=str(self.g.value(m_uri, schema.MC.name)),
            version=str(self.g.value(m_uri, schema.MC.version)),
            toolchain_id=self._tc_id(tc_uri) if tc_uri else None,
            installed=inst.toPython() if inst is not None else True,
        )

    def modules_providing(self, ingredient, kind):
        """Modules providing a tool (kind='tool') or package ('python'/'r')."""
        ingredient = ingredient.lower()
        results = []
        if kind == "tool":
            sw = schema.software_uri(ingredient)
            for m in self.g.subjects(schema.MC.providesSoftware, sw):
                results.append(self._module_ref(m))
        else:
            p = schema.package_uri(ingredient, kind)
            for m in self.g.subjects(schema.MC.providesPackage, p):
                results.append(self._module_ref(m))
        return results

    def dependencies_of(self, module_uri):
        from rdflib import URIRef
        deps = []
        for d in self.g.objects(URIRef(module_uri), schema.MC.dependsOn):
            full = self.g.value(d, schema.MC.fullName)
            if full:
                deps.append(ModuleRef(str(d), str(full), str(full).split("/")[0],
                                      "", None))
        return deps

    def compatible_toolchains(self, tc_id):
        return toolchains.ancestors(tc_id, self._sub_hierarchy)

    def all_software(self):
        """All software names that any module provides."""
        names = set()
        for sw in self.g.objects(None, schema.MC.providesSoftware):
            n = self.g.value(sw, schema.MC.name)
            if n is not None:
                names.add(str(n))
        return sorted(names)

    def all_packages(self):
        """All (package_name, ecosystem) pairs that any module provides."""
        out = []
        for p in set(self.g.objects(None, schema.MC.providesPackage)):
            name = self.g.value(p, schema.MC.name)
            eco = self.g.value(p, schema.MC.ecosystem)
            if name is not None:
                out.append((str(name), str(eco) if eco else None))
        return sorted(out)

    def all_module_names(self):
        """All module full names known to the graph (incl. dependency stubs)."""
        names = set()
        for _, _, full in self.g.triples((None, schema.MC.fullName, None)):
            names.add(str(full))
        return sorted(names)

    def search(self, term):
        """Return ModuleRefs whose software name contains `term`."""
        term = term.lower()
        out = []
        for sw in set(self.g.objects(None, schema.MC.providesSoftware)):
            n = self.g.value(sw, schema.MC.name)
            if n is not None and term in str(n):
                for m in self.g.subjects(schema.MC.providesSoftware, sw):
                    out.append(self._module_ref(m))
        return out

    def modules_by_full_name(self, full_name):
        m = schema.module_uri(full_name)
        if (m, schema.MC.fullName, Literal(full_name)) in self.g:
            return self._module_ref(m)
        return None

    def provided_packages(self, full_name):
        from rdflib import URIRef
        m = schema.module_uri(full_name)
        out = []
        for p in self.g.objects(m, schema.MC.providesPackage):
            name = self.g.value(p, schema.MC.name)
            eco = self.g.value(p, schema.MC.ecosystem)
            if name is not None:
                out.append((str(name), str(eco) if eco else None))
        return sorted(out)
