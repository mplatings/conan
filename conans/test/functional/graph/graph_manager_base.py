import os
import unittest
from collections import namedtuple

from mock import Mock

from conans.client.cache.cache import ClientCache
from conans.client.cache.remote_registry import Remotes
from conans.client.graph.graph_binaries import GraphBinariesAnalyzer
from conans.client.graph.graph_manager import GraphManager
from conans.client.graph.proxy import ConanProxy
from conans.client.graph.python_requires import ConanPythonRequire
from conans.client.graph.range_resolver import RangeResolver
from conans.client.installer import BinaryInstaller
from conans.client.loader import ConanFileLoader
from conans.client.recorder.action_recorder import ActionRecorder
from conans.model.graph_info import GraphInfo
from conans.model.manifest import FileTreeManifest
from conans.model.options import OptionsValues
from conans.model.profile import Profile
from conans.model.ref import ConanFileReference
from conans.test.unittests.model.transitive_reqs_test import MockRemoteManager
from conans.test.utils.test_files import temp_folder
from conans.test.utils.tools import TestBufferConanOutput, GenConanfile
from conans.util.files import save


class GraphManagerTest(unittest.TestCase):

    def setUp(self):
        self.output = TestBufferConanOutput()
        cache_folder = temp_folder()
        cache = ClientCache(cache_folder, self.output)
        self.cache = cache

    def _get_app(self):
        self.remote_manager = MockRemoteManager()
        cache = self.cache
        self.resolver = RangeResolver(self.cache, self.remote_manager)
        proxy = ConanProxy(cache, self.output, self.remote_manager)
        self.loader = ConanFileLoader(None, self.output, ConanPythonRequire(None, None))
        binaries = GraphBinariesAnalyzer(cache, self.output, self.remote_manager)
        self.manager = GraphManager(self.output, cache, self.remote_manager, self.loader, proxy,
                                    self.resolver, binaries)
        hook_manager = Mock()
        recorder = Mock()
        app_type = namedtuple("ConanApp", "cache out remote_manager hook_manager graph_manager")
        app = app_type(self.cache, self.output, self.remote_manager, hook_manager, self.manager)
        return app

    def _cache_recipe(self, ref, test_conanfile, revision=None):
        if isinstance(test_conanfile, GenConanfile):
            name, version = test_conanfile._name, test_conanfile._version
            test_conanfile = test_conanfile.with_package_info(
                cpp_info={"libs": ["mylib{}{}lib".format(name, version)]},
                env_info={"MYENV": ["myenv{}{}env".format(name, version)]})
        save(self.cache.package_layout(ref).conanfile(), str(test_conanfile))
        with self.cache.package_layout(ref).update_metadata() as metadata:
            metadata.recipe.revision = revision or "123"
        manifest = FileTreeManifest.create(self.cache.package_layout(ref).export())
        manifest.save(self.cache.package_layout(ref).export())

    def build_graph(self, content, profile_build_requires=None, ref=None, create_ref=None,
                    install=True):
        path = temp_folder()
        path = os.path.join(path, "conanfile.py")
        save(path, str(content))

        profile = Profile()
        if profile_build_requires:
            profile.build_requires = profile_build_requires
        profile.process_settings(self.cache)
        update = check_updates = False
        recorder = ActionRecorder()
        remotes = Remotes()
        build_mode = []  # Means build all
        ref = ref or ConanFileReference(None, None, None, None, validate=False)
        options = OptionsValues()
        graph_info = GraphInfo(profile, options, root_ref=ref)
        app = self._get_app()
        deps_graph, _ = app.graph_manager.load_graph(path, create_ref, graph_info,
                                                     build_mode, check_updates, update,
                                                     remotes, recorder)
        if install:
            binary_installer = BinaryInstaller(app, recorder)
            binary_installer.install(deps_graph, None, False, graph_info)
        return deps_graph

    def _check_node(self, node, ref, deps, build_deps, dependents, closure):
        conanfile = node.conanfile
        ref = ConanFileReference.loads(str(ref))
        self.assertEqual(repr(node.ref), repr(ref))
        self.assertEqual(conanfile.name, ref.name)
        self.assertEqual(len(node.dependencies), len(deps) + len(build_deps))

        dependants = node.inverse_neighbors()
        self.assertEqual(len(dependants), len(dependents))
        for d in dependents:
            self.assertIn(d, dependants)

        # The recipe requires is resolved to the reference WITH revision!
        self.assertEqual(len(deps), len(conanfile.requires))
        for dep in deps:
            self.assertEqual(conanfile.requires[dep.name].ref,
                             dep.ref)

        self.assertEqual(closure, node.public_closure)
        libs = []
        envs = []
        for n in closure:
            libs.append("mylib%s%slib" % (n.ref.name, n.ref.version))
            envs.append("myenv%s%senv" % (n.ref.name, n.ref.version))
        self.assertEqual(conanfile.deps_cpp_info.libs, libs)
        env = {"MYENV": envs} if envs else {}
        self.assertEqual(conanfile.deps_env_info.vars, env)
