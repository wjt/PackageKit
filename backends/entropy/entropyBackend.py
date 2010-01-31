#!/usr/bin/python2
# -*- coding: utf-8 -*-
#
#
# Copyright (C) 2009 Mounir Lamouri (volkmar) <mounir.lamouri@gmail.com>
# Copyright (C) 2010 Fabio Erculiani (lxnay) <lxnay@sabayon.org>
#
# Licensed under the GNU General Public License Version 2
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

import sys
import signal

from packagekit.backend import *
from packagekit.progress import *
from packagekit.package import PackagekitPackage

sys.path.insert(0, '/usr/lib/entropy/libraries')

from entropy.const import etpConst
import entropy.tools
from entropy.client.interfaces import Client
from entropy.core.settings.base import SystemSettings
from entropy.misc import LogFile

# TODO:
# remove percentage(None) if percentage is used
# protection against signal when installing/removing

class PackageKitEntropyBackend(PackageKitBaseBackend):

    _log_fname = os.path.join(etpConst['syslogdir'], "packagekit.log")

    def __sigquit(self, signum, frame):
        if hasattr(self, '_entropy'):
            self._entropy.destroy()
        raise SystemExit(1)

    def __init__(self, args):
        signal.signal(signal.SIGQUIT, self.__sigquit)
        self._entropy = Client()
        self._settings = SystemSettings()
        self._entropy_log = LogFile(
            level = self._settings['system']['log_level'],
            filename = self._log_fname, header = "[packagekit]")

        PackageKitBaseBackend.__init__(self, args)

    def _is_repository_enabled(self, repo_name):
        repo_data = self._settings['repositories']
        return repo_name in repo_data['available']

    def _get_group(self, dep):
        """
        Return PackageKit group belonging to given dependency.
        """
        category = entropy.tools.dep_getcat(dep)
        entropy_groups = self._entropy.get_package_groups()

        generic_group_name = None
        for group_key, group_data in entropy_groups.items():
            if category in group_data['categories']:
                generic_group_name = group_key
                break

        if generic_group_name is None:
            return GROUP_UNKNOWN

        pk_group_map = {
            'accessibility': GROUP_ACCESSIBILITY,
            'development': GROUP_PROGRAMMING,
            'games': GROUP_GAMES,
            'gnome': GROUP_DESKTOP_GNOME,
            'kde': GROUP_DESKTOP_KDE,
            'lxde': GROUP_DESKTOP_OTHER,
            'multimedia': GROUP_MULTIMEDIA,
            'networking': GROUP_NETWORK,
            'office': GROUP_OFFICE,
            'science': GROUP_SCIENCE,
            'system': GROUP_SYSTEM,
            'x11': GROUP_OTHER,
            'xfce': GROUP_DESKTOP_XFCE,
        }
        return pk_group_map[generic_group_name]

    def _log_message(self, source, message):
        """
        Write log message to Entropy PackageKit log file.
        """
        self._entropy_log.write("%s: %s" % (source, message,))

    def _get_percentage(self, count, max_count):
        """
        Prepare percentage value used to feed self.percentage()
        """
        return int((float(count)/max_count)*100)

    def is_cpv_valid(self, cpv):
        if self.is_installed(cpv):
            # actually if is_installed return True that means cpv is in db
            return True
        elif self.pvar.portdb.cpv_exists(cpv):
            return True

        return False

    def get_real_license_str(self, cpv, metadata):
        # use conditionals info (w/ USE) in LICENSE and remove ||
        ebuild_settings = self.get_ebuild_settings(cpv, metadata)
        license = set(portage.flatten(portage.dep.use_reduce(
            portage.dep.paren_reduce(metadata["LICENSE"]),
            uselist=ebuild_settings.get("USE", "").split())))
        license.discard('||')
        license = ' '.join(license)

        return license

    def send_configuration_file_message(self):
        result = list(portage.util.find_updated_config_files(
            self.pvar.settings['ROOT'],
            self.pvar.settings.get('CONFIG_PROTECT', '').split()))

        if result:
            message = "Some configuration files need updating."
            message += ";You should use Gentoo's tools to update them (dispatch-conf)"
            message += ";If you can't do that, ask your system administrator."
            self.message(MESSAGE_CONFIG_FILES_CHANGED, message)

    def get_restricted_fetch_files(self, cpv, metadata):
        '''
        This function checks files in SRC_URI and look if they are in DESTDIR.
        Missing files are returned. If there is no issue, None is returned.
        We don't care about digest but only about existance of files.

        NOTES:
        - we are assuming the package has RESTRICT='fetch'
          be sure to call this function only in this case.
        - we are not using fetch_check because it's not returning missing files
          so this function is a simplist fetch_check
        '''
        missing_files = []
        ebuild_settings = self.get_ebuild_settings(cpv, metadata)

        files = self.pvar.portdb.getFetchMap(cpv,
                ebuild_settings['USE'].split())

        for f in files:
            file_path = os.path.join(ebuild_settings["DISTDIR"], f)
            if not os.access(file_path, os.F_OK):
                missing_files.append([file_path, files[f]])

        if len(missing_files) > 0:
            return missing_files

        return None

    def check_fetch_restrict(self, packages_list):
        for p in packages_list:
            if 'fetch' in p.metadata['RESTRICT']:
                files = self.get_restricted_fetch_files(p.cpv, p.metadata)
                if files:
                    message = "Package %s can't download some files." % p.cpv
                    message += ";Please, download manually the followonig file(s):"
                    for x in files:
                        message += ";- %s then copy it to %s" % (' '.join(x[1]), x[0])
                    self.error(ERROR_RESTRICTED_DOWNLOAD, message)

    def elog_listener(self, settings, key, logentries, fulltext):
        '''
        This is a listener for elog.
        It's called each time elog is emitting log messages (at end of process).
        We are not using settings and fulltext but they are used by other
        listeners so we have to keep them as arguments.
        '''
        message = "Messages for package %s:;" % str(key)
        error_message = ""

        # building the message
        for phase in logentries:
            for entries in logentries[phase]:
                type = entries[0]
                messages = entries[1]

                # TODO: portage.elog.filtering is using upper() should we ?
                if type == 'LOG':
                    message += ";Information messages:"
                elif type == 'WARN':
                    message += ";Warning messages:"
                elif type == 'QA':
                    message += ";QA messages:"
                elif type == 'ERROR':
                    message += ";Error messages:"
                    self._error_phase = phase
                else:
                    continue

                for msg in messages:
                    msg = msg.replace('\n', '')
                    if type == 'ERROR':
                        error_message += msg + ";"
                    message += "; " + msg

        # add the message to the stack
        self._elog_messages.append(message)
        self._error_message = message

    def send_merge_error(self, default):
        # EAPI-2 compliant (at least)
        # 'other' phase is ignored except this one, every phase should be there
        if self._error_phase in ("setup", "unpack", "prepare", "configure",
            "nofetch", "config", "info"):
            error_type = ERROR_PACKAGE_FAILED_TO_CONFIGURE
        elif self._error_phase in ("compile", "test"):
            error_type = ERROR_PACKAGE_FAILED_TO_BUILD
        elif self._error_phase in ("install", "preinst", "postinst",
            "package"):
            error_type = ERROR_PACKAGE_FAILED_TO_INSTALL
        elif self._error_phase in ("prerm", "postrm"):
            error_type = ERROR_PACKAGE_FAILED_TO_REMOVE
        else:
            error_type = default

        self.error(error_type, self._error_message)

    def get_file_list(self, cpv):
        cat, pv = portage.catsplit(cpv)
        db = portage.dblink(cat, pv, self.pvar.settings['ROOT'],
                self.pvar.settings, treetype="vartree",
                vartree=self.pvar.vardb)

        contents = db.getcontents()
        if not contents:
            return []

        return db.getcontents().keys()

    def cmp_cpv(self, cpv1, cpv2):
        '''
        returns 1 if cpv1 > cpv2
        returns 0 if cpv1 = cpv2
        returns -1 if cpv1 < cpv2
        '''
        return portage.pkgcmp(portage.pkgsplit(cpv1), portage.pkgsplit(cpv2))

    def get_newest_cpv(self, cpv_list, installed):
        newer = ""

        # get the first cpv following the installed rule
        for cpv in cpv_list:
            if self.is_installed(cpv) == installed:
                newer = cpv
                break

        if newer == "":
            return ""

        for cpv in cpv_list:
            if self.is_installed(cpv) == installed:
                if self.cmp_cpv(cpv, newer) == 1:
                    newer = cpv

        return newer

    def get_metadata(self, cpv, keys, in_dict = False, add_cache_keys = False):
        '''
        This function returns required metadata.
        If in_dict is True, metadata is returned in a dict object.
        If add_cache_keys is True, cached keys are added to keys in parameter.
        '''
        if self.is_installed(cpv):
            aux_get = self.pvar.vardb.aux_get
            if add_cache_keys:
                keys.extend(list(self.pvar.vardb._aux_cache_keys))
        else:
            aux_get = self.pvar.portdb.aux_get
            if add_cache_keys:
                keys.extend(list(self.pvar.portdb._aux_cache_keys))

        if in_dict:
            return dict(keys, aux_get(cpv, keys))
        else:
            return aux_get(cpv, keys)

    def get_size(self, cpv):
        '''
        Returns the installed size if the package is installed.
        Otherwise, the size of files needed to be downloaded.
        If some required files have been downloaded,
        only the remaining size will be considered.
        '''
        size = 0
        if self.is_installed(cpv):
            size = self.get_metadata(cpv, ["SIZE"])[0]
            if size == '':
                size = 0
            else:
                size = int(size)
        else:
            self
            metadata = self.get_metadata(cpv, ["IUSE", "SLOT"], in_dict=True)

            package = _emerge.Package.Package(
                    type_name="ebuild",
                    built=False,
                    installed=False,
                    root_config=self.pvar.root_config,
                    cpv=cpv,
                    metadata=metadata)

            fetch_file = self.pvar.portdb.getfetchsizes(package[2],
                    package.use.enabled)
            for f in fetch_file:
                size += fetch_file[f]

        return size

    def get_cpv_slotted(self, cpv_list):
        cpv_dict = {}

        for cpv in cpv_list:
            slot = self.get_metadata(cpv, ["SLOT"])[0]
            if slot not in cpv_dict:
                cpv_dict[slot] = [cpv]
            else:
                cpv_dict[slot].append(cpv)

        return cpv_dict

    def filter_free(self, cpv_list, fltlist):
        if len(cpv_list) == 0:
            return cpv_list

        def _has_validLicense(cpv):
            metadata = self.get_metadata(cpv, ["LICENSE", "USE", "SLOT"], True)
            return not self.pvar.settings._getMissingLicenses(cpv, metadata)

        if FILTER_FREE in fltlist or FILTER_NOT_FREE in fltlist:
            free_licenses = "@FSF-APPROVED"
            if FILTER_FREE in fltlist:
                licenses = "-* " + free_licenses
            elif FILTER_NOT_FREE in fltlist:
                licenses = "* -" + free_licenses
            backup_license = self.pvar.settings["ACCEPT_LICENSE"]

            self.pvar.settings.unlock()
            self.pvar.settings["ACCEPT_LICENSE"] = licenses
            self.pvar.settings.backup_changes("ACCEPT_LICENSE")
            self.pvar.settings.regenerate()

            cpv_list = filter(_has_validLicense, cpv_list)

            self.pvar.settings["ACCEPT_LICENSE"] = backup_license
            self.pvar.settings.backup_changes("ACCEPT_LICENSE")
            self.pvar.settings.regenerate()
            self.pvar.settings.lock()

        return cpv_list

    def filter_newest(self, cpv_list, fltlist):
        if len(cpv_list) == 0:
            return cpv_list

        if FILTER_NEWEST not in fltlist:
            return cpv_list

        if FILTER_INSTALLED in fltlist:
            # we have one package per slot, so it's the newest
            return cpv_list

        cpv_dict = self.get_cpv_slotted(cpv_list)

        # slots are sorted (dict), revert them to have newest slots first
        slots = cpv_dict.keys()
        slots.reverse()

        # empty cpv_list, cpv are now in cpv_dict and cpv_list gonna be repop
        cpv_list = []

        for k in slots:
            # if not_intalled on, no need to check for newest installed
            if FILTER_NOT_INSTALLED not in fltlist:
                newest_installed = self.get_newest_cpv(cpv_dict[k], True)
                if newest_installed != "":
                    cpv_list.append(newest_installed)
            newest_available = self.get_newest_cpv(cpv_dict[k], False)
            if newest_available != "":
                cpv_list.append(newest_available)

        return cpv_list

    def get_all_cp(self, fltlist):
        # NOTES:
        # returns a list of cp
        #
        # FILTERS:
        # - installed: ok
        # - free: ok (should be done with cpv)
        # - newest: ok (should be finished with cpv)
        cp_list = []

        if FILTER_INSTALLED in fltlist:
            cp_list = self.pvar.vardb.cp_all()
        elif FILTER_NOT_INSTALLED in fltlist:
            cp_list = self.pvar.portdb.cp_all()
        else:
            # need installed packages first
            cp_list = self.pvar.vardb.cp_all()
            for cp in self.pvar.portdb.cp_all():
                if cp not in cp_list:
                    cp_list.append(cp)

        return cp_list

    def get_all_cpv(self, cp, fltlist, filter_newest=True):
        # NOTES:
        # returns a list of cpv
        #
        # FILTERS:
        # - installed: ok
        # - free: ok
        # - newest: ok

        cpv_list = []

        # populate cpv_list taking care of installed filter
        if FILTER_INSTALLED in fltlist:
            cpv_list = self.pvar.vardb.match(cp)
        elif FILTER_NOT_INSTALLED in fltlist:
            for cpv in self.pvar.portdb.match(cp):
                if not self.is_installed(cpv):
                    cpv_list.append(cpv)
        else:
            cpv_list = self.pvar.vardb.match(cp)
            for cpv in self.pvar.portdb.match(cp):
                if cpv not in cpv_list:
                    cpv_list.append(cpv)

        # free filter
        cpv_list = self.filter_free(cpv_list, fltlist)

        # newest filter
        if filter_newest:
            cpv_list = self.filter_newest(cpv_list, fltlist)

        return cpv_list

    def _etp_to_id(self, pkg_match):
        """
        Transform an Entropy package match (pkg_id, EntropyRepository) into
        PackageKit id.
        @param pkg_match: tuple composed by package identifier and its parent
            EntropyRepository instance
        @type pkg_match: tuple
        @return: PackageKit package id
        @rtype: string
        """
        pkg_id, c_repo = pkg_match

        pkg_key, pkg_slot, pkg_ver, pkg_tag, pkg_rev, atom = \
            c_repo.getStrictData(pkg_id)

        if pkg_tag:
            pkg_ver += "%s%s" % (etpConst['entropytagprefix'], pkg_tag)
            pkg_ver += "%s%s" % (etpConst['entropyslotprefix'], pkg_slot,)
        cur_arch = etpConst['currentarch']
        repo_name = c_repo.get_plugins_metadata().get("repo_name")
        if repo_name is None:
            self.error(ERROR_PACKAGE_ID_INVALID,
                "Invalid metadata passed")

        # if installed, repo should be 'installed', packagekit rule
        if repo_name == etpConst['clientdbid']:
            repo_name = "installed"

        # openoffice-clipart;2.6.22;ppc64;fedora
        return get_package_id(pkg_key, pkg_ver, cur_arch, repo_name)

    def _id_to_etp(self, pkit_id):
        """
        Transform a PackageKit package id into Entropy package match.

        @param pkit_id: PackageKit package id
        @type pkit_id: string
        @return: tuple composed by package identifier and its parent
            EntropyRepository instance
        @rtype: tuple
        """
        split_data = split_package_id(pkgid)
        if len(ret) < 4:
            self.error(ERROR_PACKAGE_ID_INVALID,
                "The package id %s does not contain 4 fields" % pkgid)
        pkg_key, pkg_ver, cur_arch, repo_name = split_data
        pkg_ver, pkg_slot = pkg_ver.rsplit(":", 1)

        if repo_name == "installed":
            c_repo = self._entropy.installed_repository()
        else:
            c_repo = self._entropy.open_repository(repo_name)

        atom = pkg_key + "-" + pkg_ver + etpConst['entropyslotprefix'] + \
            pkg_slot
        pkg_id, pkg_rc = c_repo.atomMatch(atom)
        if pkg_rc != 0:
            self.error(ERROR_PACKAGE_ID_INVALID,
                "Package not found in repository")

        return pkg_id, c_repo

    def id_to_cpv(self, pkgid):
        '''
        Transform the package id (packagekit) to a cpv (portage)
        '''
        ret = split_package_id(pkgid)

        if len(ret) < 4:
            self.error(ERROR_PACKAGE_ID_INVALID,
                    "The package id %s does not contain 4 fields" % pkgid)
        if '/' not in ret[0]:
            self.error(ERROR_PACKAGE_ID_INVALID,
                    "The first field of the package id must contain a category")

        # remove slot info from version field
        version = ret[1].split(':')[0]

        return ret[0] + "-" + version

    def get_packages_required(self, cpv_input, recursive):
        '''
        Get a list of cpv and recursive parameter.
        Returns the list of packages required for cpv list.
        '''
        packages_list = []

        myopts = {}
        myopts["--selective"] = True
        myopts["--deep"] = True

        myparams = _emerge.create_depgraph_params.create_depgraph_params(
                myopts, "remove")
        depgraph = _emerge.depgraph.depgraph(self.pvar.settings,
                self.pvar.trees, myopts, myparams, None)

        # TODO: atm, using FILTER_INSTALLED because it's quicker
        # and we don't want to manage non-installed packages
        for cp in self.get_all_cp([FILTER_INSTALLED]):
            for cpv in self.get_all_cpv(cp, [FILTER_INSTALLED]):
                depgraph._dynamic_config._dep_stack.append(
                        _emerge.Dependency.Dependency(
                            atom=portage.dep.Atom('=' + cpv),
                            root=self.pvar.settings["ROOT"], parent=None))

        if not depgraph._complete_graph():
            self.error(ERROR_INTERNAL_ERROR, "Error when generating depgraph")
            return

        def _add_children_to_list(packages_list, node):
            for n in depgraph._dynamic_config.digraph.parent_nodes(node):
                if n not in packages_list \
                        and not isinstance(n, _emerge.SetArg.SetArg):
                    packages_list.append(n)
                    _add_children_to_list(packages_list, n)

        for node in depgraph._dynamic_config.digraph.__iter__():
            if isinstance(node, _emerge.SetArg.SetArg):
                continue
            if node.cpv in cpv_input:
                if recursive:
                    _add_children_to_list(packages_list, node)
                else:
                    for n in \
                            depgraph._dynamic_config.digraph.parent_nodes(node):
                        if not isinstance(n, _emerge.SetArg.SetArg):
                            packages_list.append(n)

        # remove cpv_input that may be added to the list
        def filter_cpv_input(x): return x.cpv not in cpv_input
        return filter(filter_cpv_input, packages_list)

    def _package(self, pkg_match, info=None):

        # package_id = (package_identifier, EntropyRepository)
        pkg_id, c_repo = pkg_match
        desc = c_repo.retrieveDescription(pkg_id)

        if not info:
            if c_repo is self._entropy.installed_repository():
                info = INFO_INSTALLED
            else:
                info = INFO_AVAILABLE
        return self.package(self._etp_to_id(pkg_match), info, desc)

    def get_depends(self, filters, pkgs, recursive):
        # TODO: use only myparams ?
        # TODO: improve error management / info

        # FILTERS:
        # - installed: ok
        # - free: ok
        # - newest: ignored because only one version of a package is installed

        self.status(STATUS_INFO)
        self.allow_cancel(True)
        self.percentage(None)

        fltlist = filters.split(';')

        cpv_input = []
        cpv_list = []

        for pkg in pkgs:
            cpv = self._id_to_etp(pkg)
            if not self.is_cpv_valid(cpv):
                self.error(ERROR_PACKAGE_NOT_FOUND,
                        "Package %s was not found" % pkg)
                continue
            cpv_input.append('=' + cpv)

        myopts = {}
        myopts["--selective"] = True
        myopts["--deep"] = True
        myparams = _emerge.create_depgraph_params.create_depgraph_params(
                myopts, "")

        depgraph = _emerge.depgraph.depgraph(
                self.pvar.settings, self.pvar.trees, myopts, myparams, None)
        retval, fav = depgraph.select_files(cpv_input)

        if not retval:
            self.error(ERROR_DEP_RESOLUTION_FAILED,
                    "Wasn't able to get dependency graph")
            return

        def _add_children_to_list(cpv_list, node):
            for n in depgraph._dynamic_config.digraph.child_nodes(node):
                if n not in cpv_list:
                    cpv_list.append(n)
                    _add_children_to_list(cpv_list, n)

        for cpv in cpv_input:
            for r in depgraph._dynamic_config.digraph.root_nodes():
                # TODO: remove things with @ as first char
                # TODO: or refuse SetArgs
                if not isinstance(r, _emerge.AtomArg.AtomArg):
                    continue
                if r.atom == cpv:
                    if recursive:
                        _add_children_to_list(cpv_list, r)
                    else:
                        for n in \
                                depgraph._dynamic_config.digraph.child_nodes(r):
                            for c in \
                                depgraph._dynamic_config.digraph.child_nodes(n):
                                cpv_list.append(c)

        def _filter_uninstall(cpv):
            return cpv[3] != 'uninstall'
        def _filter_installed(cpv):
            return cpv[0] == 'installed'
        def _filter_not_installed(cpv):
            return cpv[0] != 'installed'

        # removing packages going to be uninstalled
        cpv_list = filter(_filter_uninstall, cpv_list)

        # install filter
        if FILTER_INSTALLED in fltlist:
            cpv_list = filter(_filter_installed, cpv_list)
        if FILTER_NOT_INSTALLED in fltlist:
            cpv_list = filter(_filter_not_installed, cpv_list)

        # now we can change cpv_list to a real cpv list
        tmp_list = cpv_list[:]
        cpv_list = []
        for x in tmp_list:
            cpv_list.append(x[2])
        del tmp_list

        # free filter
        cpv_list = self.filter_free(cpv_list, fltlist)

        for cpv in cpv_list:
            # prevent showing input packages
            if '=' + cpv not in cpv_input:
                self._package(cpv)

    def get_details(self, pkgs):
        self.status(STATUS_INFO)
        self.allow_cancel(True)
        self.percentage(0)

        nb_pkg = float(len(pkgs))
        pkg_processed = 0.0

        for pkg in pkgs:
            cpv = self._id_to_etp(pkg)

            if not self.is_cpv_valid(cpv):
                self.error(ERROR_PACKAGE_NOT_FOUND,
                        "Package %s was not found" % pkg)
                continue

            metadata = self.get_metadata(cpv,
                    ["DESCRIPTION", "HOMEPAGE", "IUSE", "LICENSE", "SLOT"],
                    in_dict=True)
            license = self.get_real_license_str(cpv, metadata)

            self.details(self._etp_to_id(cpv), license, self._get_group(cpv),
                    metadata["DESCRIPTION"], metadata["HOMEPAGE"],
                    self.get_size(cpv))

            pkg_processed += 100.0
            self.percentage(int(pkg_processed/nb_pkg))

        self.percentage(100)

    def get_files(self, pkgs):
        self.status(STATUS_INFO)
        self.allow_cancel(True)
        self.percentage(0)

        nb_pkg = float(len(pkgs))
        pkg_processed = 0.0

        for pkg in pkgs:
            cpv = self._id_to_etp(pkg)

            if not self.is_cpv_valid(cpv):
                self.error(ERROR_PACKAGE_NOT_FOUND,
                        "Package %s was not found" % pkg)
                continue

            if not self.is_installed(cpv):
                self.error(ERROR_CANNOT_GET_FILELIST,
                        "get-files is only available for installed packages")
                continue

            files = self.get_file_list(cpv)
            files = sorted(files)
            files = ";".join(files)

            self.files(pkg, files)

            pkg_processed += 100.0
            self.percentage(int(pkg_processed/nb_pkg))

        self.percentage(100)

    def get_packages(self, filters):
        self.status(STATUS_QUERY)
        self.allow_cancel(True)
        self.percentage(0)

        fltlist = filters.split(';')
        cp_list = self.get_all_cp(fltlist)
        nb_cp = float(len(cp_list))
        cp_processed = 0.0

        for cp in self.get_all_cp(fltlist):
            for cpv in self.get_all_cpv(cp, fltlist):
                self._package(cpv)

            cp_processed += 100.0
            self.percentage(int(cp_processed/nb_cp))

        self.percentage(100)

    def get_repo_list(self, filters):
        # NOTES:
        # use layman API
        # returns only official and supported repositories
        # and creates a dummy repo for portage tree
        self.status(STATUS_INFO)
        self.allow_cancel(True)
        self.percentage(None)

        fltlist = filters.split(';')

        # get installed and available dbs
        installed_layman_db = layman.db.DB(layman.config.Config())
        available_layman_db = layman.db.RemoteDB(layman.config.Config())

        # 'gentoo' is a dummy repo
        self.repo_detail('gentoo', 'Gentoo Portage tree', True)

        if FILTER_NOT_DEVELOPMENT not in fltlist:
            for o in available_layman_db.overlays.keys():
                if available_layman_db.overlays[o].is_official() \
                        and available_layman_db.overlays[o].is_supported():
                    self.repo_detail(o, o,
                            self._is_repository_enabled(o))

    def get_requires(self, filters, pkgs, recursive):
        # TODO: manage non-installed package

        # FILTERS:
        # - installed: error atm, see previous TODO
        # - free: ok
        # - newest: ignored because only one version of a package is installed

        self.status(STATUS_RUNNING)
        self.allow_cancel(True)
        self.percentage(None)

        fltlist = filters.split(';')

        cpv_input = []
        cpv_list = []

        if FILTER_NOT_INSTALLED in fltlist:
            self.error(ERROR_CANNOT_GET_REQUIRES,
                    "get-requires returns only installed packages at the moment")
            return

        for pkg in pkgs:
            cpv = self._id_to_etp(pkg)

            if not self.is_cpv_valid(cpv):
                self.error(ERROR_PACKAGE_NOT_FOUND,
                        "Package %s was not found" % pkg)
                continue
            if not self.is_installed(cpv):
                self.error(ERROR_CANNOT_GET_REQUIRES,
                        "get-requires is only available for installed packages at the moment")
                continue

            cpv_input.append(cpv)

        packages_list = self.get_packages_required(cpv_input, recursive)

        # now we can populate cpv_list
        cpv_list = []
        for p in packages_list:
            cpv_list.append(p.cpv)
        del packages_list

        # free filter
        cpv_list = self.filter_free(cpv_list, fltlist)

        for cpv in cpv_list:
            # prevent showing input packages
            if '=' + cpv not in cpv_input:
                self._package(cpv)

    def get_update_detail(self, pkgs):
        # TODO: a lot of informations are missing

        self.status(STATUS_INFO)
        self.allow_cancel(True)
        self.percentage(None)

        for pkg in pkgs:
            updates = []
            obsoletes = ""
            vendor_url = ""
            bugzilla_url = ""
            cve_url = ""

            cpv = self._id_to_etp(pkg)

            if not self.pvar.portdb.cpv_exists(cpv):
                self.message(MESSAGE_COULD_NOT_FIND_PACKAGE, "could not find %s" % pkg)

            for cpv in self.pvar.vardb.match(portage.pkgsplit(cpv)[0]):
                updates.append(cpv)
            updates = "&".join(updates)

            # temporarily set vendor_url = homepage
            homepage = self.get_metadata(cpv, ["HOMEPAGE"])[0]
            vendor_url = homepage

            self.update_detail(pkg, updates, obsoletes, vendor_url, bugzilla_url,
                    cve_url, "none", "No update text", "No ChangeLog",
                    UPDATE_STATE_STABLE, None, None)

    def get_updates(self, filters):
        # NOTES:
        # because of a lot of things related to Gentoo,
        # only world and system packages are can be listed as updates
        # _except_ for security updates

        # UPDATE TYPES:
        # - blocked: wait for feedbacks
        # - low: TODO: --newuse
        # - normal: default
        # - important: none atm
        # - security: from @security

        # FILTERS:
        # - installed: try to update non-installed packages and call me ;)
        # - free: ok
        # - newest: ok

        self.status(STATUS_INFO)
        self.allow_cancel(True)
        self.percentage(None)

        fltlist = filters.split(';')

        update_candidates = []
        cpv_updates = {}
        cpv_downgra = {}

        # get system and world packages
        for s in ["system", "world"]:
            set = portage.sets.base.InternalPackageSet(
                    initial_atoms=self.pvar.root_config.setconfig.getSetAtoms(s))
            for atom in set:
                update_candidates.append(atom.cp)

        # check if a candidate can be updated
        for cp in update_candidates:
            cpv_list_inst = self.pvar.vardb.match(cp)
            cpv_list_avai = self.pvar.portdb.match(cp)

            cpv_dict_inst = self.get_cpv_slotted(cpv_list_inst)
            cpv_dict_avai = self.get_cpv_slotted(cpv_list_avai)

            dict_upda = {}
            dict_down = {}

            # candidate slots are installed slots
            slots = cpv_dict_inst.keys()
            slots.reverse()

            for s in slots:
                cpv_list_updates = []
                cpv_inst = cpv_dict_inst[s][0] # only one install per slot

                # the slot can be outdated (not in the tree)
                if s not in cpv_dict_avai:
                    break

                tmp_list_avai = cpv_dict_avai[s]
                tmp_list_avai.reverse()

                for cpv in tmp_list_avai:
                    if self.cmp_cpv(cpv_inst, cpv) == -1:
                        cpv_list_updates.append(cpv)
                    else: # because the list is sorted
                        break

                # no update for this slot
                if len(cpv_list_updates) == 0:
                    if [cpv_inst] == self.pvar.portdb.visible([cpv_inst]):
                        break # really no update
                    else:
                        # that's actually a downgrade or even worst
                        if len(tmp_list_avai) == 0:
                            break # this package is not known in the tree...
                        else:
                            dict_down[s] = [tmp_list_avai.pop()]

                cpv_list_updates = self.filter_free(cpv_list_updates, fltlist)

                if len(cpv_list_updates) == 0:
                    break

                if FILTER_NEWEST in fltlist:
                    best_cpv = portage.best(cpv_list_updates)
                    cpv_list_updates = [best_cpv]

                dict_upda[s] = cpv_list_updates

            if len(dict_upda) != 0:
                cpv_updates[cp] = dict_upda
            if len(dict_down) != 0:
                cpv_downgra[cp] = dict_down

        # get security updates
        for atom in portage.sets.base.InternalPackageSet(
                initial_atoms=self.pvar.root_config.setconfig.getSetAtoms("security")):
            # send update message and remove atom from cpv_updates
            if atom.cp in cpv_updates:
                slot = self.get_metadata(atom.cpv, ["SLOT"])[0]
                if slot in cpv_updates[atom.cp]:
                    tmp_cpv_list = cpv_updates[atom.cp][slot][:]
                    for cpv in tmp_cpv_list:
                        if self.cmp_cpv(cpv, atom.cpv) >= 0:
                            # cpv is a security update and removed from list
                            cpv_updates[atom.cp][slot].remove(cpv)
                            self._package(cpv, INFO_SECURITY)
            else: # update also non-world and non-system packages if security
                self._package(atom.cpv, INFO_SECURITY)

        # downgrades
        for cp in cpv_downgra:
            for slot in cpv_downgra[cp]:
                for cpv in cpv_downgra[cp][slot]:
                    self._package(cpv, INFO_IMPORTANT)

        # normal updates
        for cp in cpv_updates:
            for slot in cpv_updates[cp]:
                for cpv in cpv_updates[cp][slot]:
                    self._package(cpv, INFO_NORMAL)

    def install_packages(self, only_trusted, pkgs):
        # NOTES:
        # can't install an already installed packages
        # even if it happens to be needed in Gentoo but probably not this API

        self.status(STATUS_RUNNING)
        self.allow_cancel(False)
        self.percentage(None)

        cpv_list = []

        for pkg in pkgs:
            cpv = self._id_to_etp(pkg)

            if not self.is_cpv_valid(cpv):
                self.error(ERROR_PACKAGE_NOT_FOUND,
                        "Package %s was not found" % pkg)
                continue

            if self.is_installed(cpv):
                self.error(ERROR_PACKAGE_ALREADY_INSTALLED,
                        "Package %s is already installed" % pkg)
                continue

            cpv_list.append('=' + cpv)

        # only_trusted isn't supported
        # but better to show it after important errors
        if only_trusted:
            self.error(ERROR_MISSING_GPG_SIGNATURE,
                    "Portage backend does not support GPG signature")
            return

        # creating installation depgraph
        myopts = {}
        favorites = []
        myparams = _emerge.create_depgraph_params.create_depgraph_params(
                myopts, "")

        self.status(STATUS_DEP_RESOLVE)

        depgraph = _emerge.depgraph.depgraph(self.pvar.settings,
                self.pvar.trees, myopts, myparams, None)
        retval, favorites = depgraph.select_files(cpv_list)
        if not retval:
            self.error(ERROR_DEP_RESOLUTION_FAILED,
                    "Wasn't able to get dependency graph")
            return

        # check fetch restrict, can stop the function via error signal
        self.check_fetch_restrict(depgraph.altlist())

        self.status(STATUS_INSTALL)

        # get elog messages
        portage.elog.add_listener(self.elog_listener)

        try:
            self.block_output()
            # compiling/installing
            mergetask = _emerge.Scheduler.Scheduler(self.pvar.settings,
                    self.pvar.trees, self.pvar.mtimedb, myopts, None,
                    depgraph.altlist(), favorites, depgraph.schedulerGraph())
            rval = mergetask.merge()
        finally:
            self.unblock_output()

        # when an error is found print error messages
        if rval != os.EX_OK:
            self.send_merge_error(ERROR_PACKAGE_FAILED_TO_INSTALL)

        # show elog messages and clean
        portage.elog.remove_listener(self.elog_listener)
        for msg in self._elog_messages:
            # TODO: use specific message ?
            self.message(MESSAGE_UNKNOWN, msg)
        self._elog_messages = []

        self.send_configuration_file_message()

    def refresh_cache(self, force):
        # NOTES: can't manage progress even if it could be better
        # TODO: do not wait for exception, check timestamp
        # TODO: message if overlay repo has changed (layman)
        self.status(STATUS_REFRESH_CACHE)
        self.allow_cancel(False)
        self.percentage(None)

        myopts = {'--quiet': True}

        # get installed and available dbs
        installed_layman_db = layman.db.DB(layman.config.Config())

        if force:
            timestamp_path = os.path.join(
                    self.pvar.settings["PORTDIR"], "metadata", "timestamp.chk")
            if os.access(timestamp_path, os.F_OK):
                os.remove(timestamp_path)

        try:
            self.block_output()
            for o in installed_layman_db.overlays.keys():
                installed_layman_db.sync(o, quiet=True)
            _emerge.actions.action_sync(self.pvar.settings, self.pvar.trees,
                    self.pvar.mtimedb, myopts, "")
        except:
            self.error(ERROR_INTERNAL_ERROR, traceback.format_exc())
        finally:
            self.unblock_output()

    def remove_packages(self, allowdep, autoremove, pkgs):
        self.status(STATUS_RUNNING)
        self.allow_cancel(False)
        self.percentage(None)

        cpv_list = []
        packages = []
        required_packages = []
        system_packages = []

        # get system packages
        set = portage.sets.base.InternalPackageSet(
                initial_atoms=self.pvar.root_config.setconfig.getSetAtoms("system"))
        for atom in set:
            system_packages.append(atom.cp)

        # create cpv_list
        for pkg in pkgs:
            cpv = self._id_to_etp(pkg)

            if not self.is_cpv_valid(cpv):
                self.error(ERROR_PACKAGE_NOT_FOUND,
                        "Package %s was not found" % pkg)
                continue

            if not self.is_installed(cpv):
                self.error(ERROR_PACKAGE_NOT_INSTALLED,
                        "Package %s is not installed" % pkg)
                continue

            # stop removal if a package is in the system set
            if portage.pkgsplit(cpv)[0] in system_packages:
                self.error(ERROR_CANNOT_REMOVE_SYSTEM_PACKAGE,
                        "Package %s is a system package. If you really want to remove it, please use portage" % pkg)
                continue

            cpv_list.append(cpv)

        # backend do not implement autoremove
        if autoremove:
            self.message(MESSAGE_AUTOREMOVE_IGNORED,
                    "Portage backend do not implement autoremove option")

        # get packages needing candidates for removal
        required_packages = self.get_packages_required(cpv_list, recursive=True)

        # if there are required packages, allowdep must be on
        if required_packages and not allowdep:
            self.error(ERROR_DEP_RESOLUTION_FAILED,
                    "Could not perform remove operation has packages are needed by other packages")
            return

        # first, we add required packages
        for p in required_packages:
            package = _emerge.Package.Package(
                    type_name=p.type_name,
                    built=p.built,
                    installed=p.installed,
                    root_config=p.root_config,
                    cpv=p.cpv,
                    metadata=p.metadata,
                    operation='uninstall')
            packages.append(package)

        # and now, packages we want really to remove
        for cpv in cpv_list:
            metadata = self.get_metadata(cpv, [],
                    in_dict=True, add_cache_keys=True)
            package = _emerge.Package.Package(
                    type_name="ebuild",
                    built=True,
                    installed=True,
                    root_config=self.pvar.root_config,
                    cpv=cpv,
                    metadata=metadata,
                    operation="uninstall")
            packages.append(package)

        # need to define favorites to remove packages from world set
        favorites = []
        for p in packages:
            favorites.append('=' + p.cpv)

        # get elog messages
        portage.elog.add_listener(self.elog_listener)

        # now, we can remove
        try:
            self.block_output()
            mergetask = _emerge.Scheduler.Scheduler(self.pvar.settings,
                    self.pvar.trees, self.pvar.mtimedb, mergelist=packages,
                    myopts={}, spinner=None, favorites=favorites, digraph=None)
            rval = mergetask.merge()
        finally:
            self.unblock_output()

        # when an error is found print error messages
        if rval != os.EX_OK:
            self.send_merge_error(ERROR_PACKAGE_FAILED_TO_REMOVE)

        # show elog messages and clean
        portage.elog.remove_listener(self.elog_listener)
        for msg in self._elog_messages:
            # TODO: use specific message ?
            self.message(MESSAGE_UNKNOWN, msg)
        self._elog_messages = []

    def repo_enable(self, repoid, enable):
        # NOTES: use layman API >= 1.2.3
        self.status(STATUS_INFO)
        self.allow_cancel(True)
        self.percentage(None)

        # special case: trying to work with gentoo repo
        if repoid == 'gentoo':
            if not enable:
                self.error(ERROR_CANNOT_DISABLE_REPOSITORY,
                        "gentoo repository can't be disabled")
            return

        # get installed and available dbs
        installed_layman_db = layman.db.DB(layman.config.Config())
        available_layman_db = layman.db.RemoteDB(layman.config.Config())

        # check now for repoid so we don't have to do it after
        if not repoid in available_layman_db.overlays.keys():
            self.error(ERROR_REPO_NOT_FOUND,
                    "Repository %s was not found" % repoid)
            return

        # disabling (removing) a db
        # if repository already disabled, ignoring
        if not enable and self._is_repository_enabled(repoid):
            try:
                installed_layman_db.delete(installed_layman_db.select(repoid))
            except Exception, e:
                self.error(ERROR_INTERNAL_ERROR,
                        "Failed to disable repository "+repoid+" : "+str(e))
                return

        # enabling (adding) a db
        # if repository already enabled, ignoring
        if enable and not self._is_repository_enabled(repoid):
            try:
                # TODO: clean the trick to prevent outputs from layman
                self.block_output()
                installed_layman_db.add(available_layman_db.select(repoid),
                        quiet=True)
                self.unblock_output()
            except Exception, e:
                self.unblock_output()
                self.error(ERROR_INTERNAL_ERROR,
                        "Failed to enable repository "+repoid+" : "+str(e))
                return

    def resolve(self, filters, pkgs):
        self.status(STATUS_QUERY)
        self.allow_cancel(True)
        self.percentage(0)

        fltlist = filters.split(';')
        cp_list = self.get_all_cp(fltlist)
        nb_cp = float(len(cp_list))
        cp_processed = 0.0

        reg_expr = []
        for pkg in pkgs:
            reg_expr.append("^" + re.escape(pkg) + "$")
        reg_expr = "|".join(reg_expr)

        # specifications says "be case sensitive"
        s = re.compile(reg_expr)

        for cp in cp_list:
            if s.match(cp):
                for cpv in self.get_all_cpv(cp, fltlist):
                    self._package(cpv)

            cp_processed += 100.0
            self.percentage(int(cp_processed/nb_cp))

        self.percentage(100)

    def search_details(self, filters, keys):
        # NOTES: very bad performance
        self.status(STATUS_QUERY)
        self.allow_cancel(True)
        self.percentage(0)

        fltlist = filters.split(';')
        cp_list = self.get_all_cp(fltlist)
        nb_cp = float(len(cp_list))
        cp_processed = 0.0
        search_list = get_search_list(keys)

        for cp in cp_list:
            # unfortunatelly, everything is related to cpv, not cp
            # can't filter cp
            cpv_list = []

            # newest filter can't be executed now
            # because some cpv are going to be filtered by search conditions
            # and newest filter could be alterated
            for cpv in self.get_all_cpv(cp, fltlist, filter_newest=False):
                match = True
                metadata =  self.get_metadata(cpv,
                        ["DESCRIPTION", "HOMEPAGE", "IUSE",
                            "LICENSE", "repository", "SLOT"],
                        in_dict=True)
                # update LICENSE to correspond to system settings
                metadata["LICENSE"] = self.get_real_license_str(cpv, metadata)
                for s in search_list:
                    found = False
                    for x in metadata:
                        if s.search(metadata[x]):
                            found = True
                            break
                    if not found:
                        match = False
                        break
                if match:
                    cpv_list.append(cpv)

            # newest filter
            cpv_list = self.filter_newest(cpv_list, fltlist)

            for cpv in cpv_list:
                self._package(cpv)

            cp_processed += 100.0
            self.percentage(int(cp_processed/nb_cp))

        self.percentage(100)

    def search_file(self, filters, key):
        # FILTERS:
        # - ~installed is not accepted (error)
        # - free: ok
        # - newest: as only installed, by himself
        self.status(STATUS_QUERY)
        self.allow_cancel(True)
        self.percentage(0)

        fltlist = filters.split(';')

        if FILTER_NOT_INSTALLED in fltlist:
            self.error(ERROR_CANNOT_GET_FILELIST,
                    "search-file isn't available with ~installed filter")
            return

        cpv_list = self.pvar.vardb.cpv_all()
        nb_cpv = 0.0
        cpv_processed = 0.0
        is_full_path = True

        if key[0] != "/":
            is_full_path = False
            key = re.escape(key)
            searchre = re.compile("/" + key + "$", re.IGNORECASE)

        # free filter
        cpv_list = self.filter_free(cpv_list, fltlist)
        nb_cpv = float(len(cpv_list))

        for cpv in cpv_list:
            for f in self.get_file_list(cpv):
                if (is_full_path and key == f) \
                or (not is_full_path and searchre.search(f)):
                    self._package(cpv)
                    break

            cpv_processed += 100.0
            self.percentage(int(cpv_processed/nb_cpv))

        self.percentage(100)

    def search_group(self, filters, group):
        # TODO: filter unknown groups before searching ? (optimization)
        self.status(STATUS_QUERY)
        self.allow_cancel(True)
        self.percentage(0)

        fltlist = filters.split(';')
        cp_list = self.get_all_cp(fltlist)
        nb_cp = float(len(cp_list))
        cp_processed = 0.0

        for cp in cp_list:
            if self._get_group(cp) == group:
                for cpv in self.get_all_cpv(cp, fltlist):
                    self._package(cpv)

            cp_processed += 100.0
            self.percentage(int(cp_processed/nb_cp))

        self.percentage(100)

    def search_name(self, filters, keys):

        self.status(STATUS_QUERY)
        self.allow_cancel(True)
        self.percentage(0)

        self._log_message(__name__, "search_name: got %s and %s" % (
            filters, keys,))

        repo_ids = self._entropy.repositories() + ["__system__"]
        repos = []
        for repo in repo_ids:
            if repo == "__system__":
                repo_db = self._entropy.installed_repository()
            else:
                repo_db = self._entropy.open_repository(repo)
            repos.append((repo_db, repo,))

        search_keys = keys.split("&")
        pkgs = set()
        count = 0
        max_count = len(repos)
        for repo_db, repo in repos:
            count += 1
            percent = self._get_percentage(count, max_count)

            self._log_message(__name__, "search_name: done %s/100" % (
                percent,))

            self.percentage(percent)
            for key in search_keys:
                pkg_ids = repo_db.searchPackages(key, just_id = True)
                pkgs.update((repo, x, repo_db,) for x in pkg_ids)

        # now filter
        fltlist = filters.split(';')
        for flt in fltlist:
            if flt == FILTER_NONE:
                continue
            elif flt == FILTER_INSTALLED:
                pkgs = set([x for x in pkgs if x[0] == "__system__"])

        lambda_sort = lambda x: x[2].retrieveAtom(x[1])

        for repo, pkg_id, c_repo in sorted(pkgs, key = lambda_sort):
            self._package((pkg_id, c_repo))

        self.percentage(100)

    def update_packages(self, only_trusted, pkgs):
        # TODO: manage errors
        # TODO: manage config file updates

        self.status(STATUS_RUNNING)
        self.allow_cancel(False)
        self.percentage(None)

        cpv_list = []

        for pkg in pkgs:
            cpv = self._id_to_etp(pkg)

            if not self.is_cpv_valid(cpv):
                self.error(ERROR_UPDATE_NOT_FOUND,
                        "Package %s was not found" % pkg)
                continue

            cpv_list.append('=' + cpv)

        # only_trusted isn't supported
        # but better to show it after important errors
        if only_trusted:
            self.error(ERROR_MISSING_GPG_SIGNATURE,
                    "Portage backend does not support GPG signature")
            return

        # creating update depgraph
        myopts = {}
        favorites = []
        myparams = _emerge.create_depgraph_params.create_depgraph_params(
                myopts, "")

        self.status(STATUS_DEP_RESOLVE)

        depgraph = _emerge.depgraph.depgraph(self.pvar.settings,
                self.pvar.trees, myopts, myparams, None)
        retval, favorites = depgraph.select_files(cpv_list)
        if not retval:
            self.error(ERROR_DEP_RESOLUTION_FAILED,
                    "Wasn't able to get dependency graph")
            return

        # check fetch restrict, can stop the function via error signal
        self.check_fetch_restrict(depgraph.altlist())

        self.status(STATUS_INSTALL)

        # get elog messages
        portage.elog.add_listener(self.elog_listener)

        try:
            self.block_output()
            # compiling/installing
            mergetask = _emerge.Scheduler.Scheduler(self.pvar.settings,
                    self.pvar.trees, self.pvar.mtimedb, myopts, None,
                    depgraph.altlist(), favorites, depgraph.schedulerGraph())
            rval = mergetask.merge()
        finally:
            self.unblock_output()

        # when an error is found print error messages
        if rval != os.EX_OK:
            self.send_merge_error(ERROR_PACKAGE_FAILED_TO_INSTALL)

        # show elog messages and clean
        portage.elog.remove_listener(self.elog_listener)
        for msg in self._elog_messages:
            # TODO: use specific message ?
            self.message(MESSAGE_UNKNOWN, msg)
        self._elog_messages = []

        self.send_configuration_file_message()

    def update_system(self, only_trusted):
        self.status(STATUS_RUNNING)
        self.allow_cancel(False)
        self.percentage(None)

        if only_trusted:
            self.error(ERROR_MISSING_GPG_SIGNATURE,
                    "Portage backend does not support GPG signature")
            return

        myopts = {}
        myopts["--deep"] = True
        myopts["--newuse"] = True
        myopts["--update"] = True

        myparams = _emerge.create_depgraph_params.create_depgraph_params(
                myopts, "")

        self.status(STATUS_DEP_RESOLVE)

        # creating list of ebuilds needed for the system update
        # using backtrack_depgraph to prevent errors
        retval, depgraph, _ = _emerge.depgraph.backtrack_depgraph(
                self.pvar.settings, self.pvar.trees, myopts, myparams, "",
                ["@system", "@world"], None)
        if not retval:
            self.error(ERROR_INTERNAL_ERROR,
                    "Wasn't able to get dependency graph")
            return

        # check fetch restrict, can stop the function via error signal
        self.check_fetch_restrict(depgraph.altlist())

        self.status(STATUS_INSTALL)

        # get elog messages
        portage.elog.add_listener(self.elog_listener)

        try:
            self.block_output()
            # compiling/installing
            mergetask = _emerge.Scheduler.Scheduler(self.pvar.settings,
                    self.pvar.trees, self.pvar.mtimedb, myopts, None,
                    depgraph.altlist(), None, depgraph.schedulerGraph())
            rval = mergetask.merge()
        finally:
            self.unblock_output()

        # when an error is found print error messages
        if rval != os.EX_OK:
            self.send_merge_error(ERROR_PACKAGE_FAILED_TO_INSTALL)

        # show elog messages and clean
        portage.elog.remove_listener(self.elog_listener)
        for msg in self._elog_messages:
            # TODO: use specific message ?
            self.message(MESSAGE_UNKNOWN, msg)
        self._elog_messages = []

        self.send_configuration_file_message()

def main():
    backend = PackageKitEntropyBackend("")
    backend.dispatcher(sys.argv[1:])

if __name__ == "__main__":
    main()
