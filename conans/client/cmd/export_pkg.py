import os

from conans.client import packager
from conans.client.graph.graph_manager import load_deps_info
from conans.errors import ConanException
from conans.model.conan_file import get_env_context_manager
from conans.model.manifest import FileTreeManifest
from conans.model.ref import PackageReference
from conans.util.files import rmdir


def export_pkg(app, recorder, full_ref, source_folder, build_folder, package_folder, install_folder,
               graph_info, force, remotes):
    ref = full_ref.copy_clear_rev()
    cache, output, hook_manager = app.cache, app.out, app.hook_manager
    graph_manager = app.graph_manager
    conan_file_path = cache.package_layout(ref).conanfile()
    if not os.path.exists(conan_file_path):
        raise ConanException("Package recipe '%s' does not exist" % str(ref))

    # The graph has to be loaded with build_mode=[ref.name], so that node is not tried
    # to be downloaded from remotes
    deps_graph, _ = graph_manager.load_graph(ref, None, graph_info=graph_info,
                                             build_mode=[ref.name], check_updates=False,
                                             update=False, remotes=remotes,
                                             recorder=recorder, apply_build_requires=False)
    # this is a bit tricky, but works. The root (virtual), has only 1 neighbor,
    # which is the exported pkg
    nodes = deps_graph.root.neighbors()
    conanfile = nodes[0].conanfile
    from conans.client.conan_api import existing_info_files
    if install_folder and existing_info_files(install_folder):
        load_deps_info(install_folder, conanfile, required=True)
    package_id = nodes[0].package_id
    output.info("Packaging to %s" % package_id)
    pref = PackageReference(ref, package_id)
    layout = cache.package_layout(ref, short_paths=conanfile.short_paths)
    dest_package_folder = layout.package(pref)

    if os.path.exists(dest_package_folder):
        if force:
            rmdir(dest_package_folder)
        else:
            raise ConanException("Package already exists. Please use --force, -f to "
                                 "overwrite it")

    recipe_hash = layout.recipe_manifest().summary_hash
    conanfile.info.recipe_hash = recipe_hash
    conanfile.develop = True
    if package_folder:
        prev = packager.export_pkg(conanfile, package_id, package_folder, dest_package_folder,
                                   hook_manager, conan_file_path, ref)
    else:
        with get_env_context_manager(conanfile):
            prev = packager.run_package_method(conanfile, package_id, source_folder, build_folder,
                                               dest_package_folder, install_folder, hook_manager,
                                               conan_file_path, ref, local=True)

    packager.update_package_metadata(prev, layout, package_id, full_ref.revision)
    pref = PackageReference(pref.ref, pref.id, prev)
    if graph_info.graph_lock:
        # after the package has been created we need to update the node PREV
        nodes[0].prev = pref.revision
        graph_info.graph_lock.update_check_graph(deps_graph, output)
    recorder.package_exported(pref)
