#!/usr/bin/python
# Copyright 2012 Gentoo Foundation
# Copyright 2012 Mike Frysinger <vapier@gentoo.org>
# Distributed under the terms of the GNU General Public License v2
# $Header: /var/cvsroot/gentoo-projects/pax-utils/lddtree.py,v 1.6 2012/11/15 19:33:08 vapier Exp $

"""Read the ELF dependency tree and show it

This does not work like `ldd` in that we do not execute/load code (only read
files on disk), and we should the ELFs as a tree rather than a flat list.
"""

from __future__ import print_function

import glob
import optparse
import os
import shutil
import sys

from elftools.elf.elffile import ELFFile
from elftools.common import exceptions


def warn(msg, prefix='warning'):
	"""Write |msg| to stderr with a |prefix| before it"""
	print('%s: %s: %s' % (sys.argv[0], prefix, msg), file=sys.stderr)


def err(msg, status=1):
	"""Write |msg| to stderr and exit with |status|"""
	warn(msg, prefix='error')
	sys.exit(status)


def normpath(path):
	"""Normalize a path

	Python's os.path.normpath() doesn't handle some cases:
		// -> //
		//..// -> //
		//..//..// -> ///
	"""
	return os.path.normpath(path).replace('//', '/')


def ParseLdPaths(str_ldpaths, root=''):
	"""Parse the colon-delimited list of paths and apply ldso rules to each

	Note the special handling as dictated by the ldso:
	 - Empty paths are equivalent to $PWD
	 - (TODO) $ORIGIN is expanded to the path of the given file
	 - (TODO) $LIB and friends

	Args:
	  str_ldpath: A colon-delimited string of paths
	  root: The path to prepend to all paths found
	Returns:
	  list of processed paths
	"""
	ldpaths = []
	for ldpath in str_ldpaths.split(':'):
		if ldpath == '':
			# The ldso treats "" paths as $PWD.
			ldpath = os.getcwd()
		ldpath = normpath(root + ldpath)
		if not ldpath in ldpaths:
			ldpaths.append(ldpath)
	return ldpaths


def ParseLdSoConf(ldso_conf, root='/', _first=True):
	"""Load all the paths from a given ldso config file

	This should handle comments, whitespace, and "include" statements.

	Args:
	  ldso_conf: The file to scan
	  root: The path to prepend to all paths found
	  _first: Recursive use only; is this the first ELF ?
	Returns:
	  list of paths found
	"""
	paths = []

	try:
		with open(ldso_conf) as f:
			for line in f.readlines():
				line = line.split('#', 1)[0].strip()
				if not line:
					continue
				if line.startswith('include '):
					line = line[8:]
					if line[0] == '/':
						line = root + line.lstrip('/')
					else:
						line = os.path.dirname(ldso_conf) + '/' + line
					for file in glob.glob(line):
						paths += ParseLdSoConf(file, root=root, _first=False)
				else:
					paths += [normpath(root + line)]
	except IOError:
		pass

	if _first:
		# Remove duplicate entries to speed things up.
		# XXX: Load paths from ldso itself.
		new_paths = []
		for path in paths:
			if not path in new_paths:
				new_paths.append(path)
		paths = new_paths

	return paths


def LoadLdpaths(root='/'):
	"""Load linker paths from common locations

	This parses the ld.so.conf and LD_LIBRARY_PATH env var.

	Args:
	  root: The root tree to prepend to paths
	Returns:
	  dict containing library paths to search
	"""
	ldpaths = {
		'conf': [],
		'env': [],
		'interp': [],
	}

	# Load up $LD_LIBRARY_PATH.
	ldpaths['env'] = []
	env_ldpath = os.environ.get('LD_LIBRARY_PATH')
	if not env_ldpath is None:
		if root != '/':
			warn('ignoring LD_LIBRARY_PATH due to ROOT usage')
		else:
			ldpaths['env'] = ParseLdPaths(env_ldpath)

	# Load up /etc/ld.so.conf.
	ldpaths['conf'] = ParseLdSoConf(root + 'etc/ld.so.conf', root=root)

	return ldpaths


def CompatibleELFs(elf1, elf2):
	"""See if two ELFs are compatible

	This compares the aspects of the ELF to see if they're compatible:
	bit size, endianness, machine type, and operating system.

	Args:
	  elf1: an ELFFile object
	  elf2: an ELFFile object
	Returns:
	  True if compatible, False otherwise
	"""
	osabi1 = elf1.header['e_ident']['EI_OSABI']
	osabi2 = elf2.header['e_ident']['EI_OSABI']
	if elf1.elfclass != elf2.elfclass or \
	   elf1.little_endian != elf2.little_endian or \
	   elf1.header['e_machine'] != elf2.header['e_machine']:
		return False
	elif osabi1 != osabi2:
		compat_sets = (
			frozenset(['ELFOSABI_NONE', 'ELFOSABI_SYSV', 'ELFOSABI_LINUX']),
		)
		for cs in compat_sets:
			cs1 = cs | set([osabi1])
			cs2 = cs | set([osabi2])
			if cs1 == cs2:
				return True
		return False
	else:
		return True


def FindLib(elf, lib, ldpaths):
	"""Try to locate a |lib| that is compatible to |elf| in the given |ldpaths|

	Args:
	  elf: the elf which the library should be compatible with (ELF wise)
	  lib: the library (basename) to search for
	  ldpaths: a list of paths to search
	Returns:
	  the full path to the desired library
	"""
	for ldpath in ldpaths:
		path = os.path.join(ldpath, lib)
		if os.path.exists(path):
			with open(path) as f:
				libelf = ELFFile(f)
				if CompatibleELFs(elf, libelf):
					return path
	return None


def ParseELF(file, root='/', ldpaths={'conf':[], 'env':[], 'interp':[]},
             _first=True, _all_libs={}):
	"""Parse the ELF dependency tree of the specified file

	Args:
	  file: The ELF to scan
	  root: The root tree to prepend to paths; this applies to interp and rpaths
	        only as |file| and |ldpaths| are expected to be prefixed already
	  ldpaths: dict containing library paths to search; should have the keys:
	           conf, env, interp
	  _first: Recursive use only; is this the first ELF ?
	  _all_libs: Recursive use only; dict of all libs we've seen
	Returns:
	  a dict containing information about all the ELFs; e.g.
		{
			'interp': '/lib64/ld-linux.so.2',
			'needed': ['libc.so.6', 'libcurl.so.4',],
			'libs': {
				'libc.so.6': {
					'path': '/lib64/libc.so.6',
					'needed': [],
				},
				'libcurl.so.4': {
					'path': '/usr/lib64/libcurl.so.4',
					'needed': ['libc.so.6', 'librt.so.1',],
				},
			},
		}	  
	"""
	ret = {
		'interp': None,
		'path': file,
		'needed': [],
		'libs': _all_libs,
	}

	with open(file) as f:
		elf = ELFFile(f)

		# If this is the first ELF, extract the interpreter.
		if _first:
			for segment in elf.iter_segments():
				if segment.header.p_type != 'PT_INTERP':
					continue

				ret['interp'] = interp = segment.get_interp_name()
				ret['libs'][os.path.basename(interp)] = {
					'path': normpath(root + interp),
					'needed': [],
				}
				# XXX: Should read it and scan for /lib paths.
				ldpaths['interp'] = [
					normpath(root + os.path.dirname(interp)),
					normpath(root + '/usr' + os.path.dirname(interp)),
				]
				break

		# Parse the ELF's dynamic tags.
		libs = []
		rpaths = []
		runpaths = []
		for segment in elf.iter_segments():
			if segment.header.p_type != 'PT_DYNAMIC':
				continue

			for t in segment.iter_tags():
				if t.entry.d_tag == 'DT_RPATH':
					rpaths = ParseLdPaths(t.rpath, root)
				elif t.entry.d_tag == 'DT_RUNPATH':
					runpaths = ParseLdPaths(t.runpath, root)
				elif t.entry.d_tag == 'DT_NEEDED':
					libs.append(t.needed)
			if runpaths:
				# If both RPATH and RUNPATH are set, only the latter is used.
				rpath = []

			break
		ret['needed'] = libs

		# Search for the libs this ELF uses.
		all_ldpaths = None
		for lib in libs:
			if lib in _all_libs:
				continue
			if all_ldpaths is None:
				all_ldpaths = rpaths + ldpaths['env'] + runpaths + ldpaths['conf'] + ldpaths['interp']
			fullpath = FindLib(elf, lib, all_ldpaths)
			_all_libs[lib] = {
				'path': fullpath,
				'needed': [],
			}
			if fullpath:
				lret = ParseELF(fullpath, root, ldpaths, False, _all_libs)
				_all_libs[lib]['needed'] = lret['needed']

		del elf

	return ret


def _NormalizePath(option, _opt, value, parser):
	setattr(parser.values, option.dest, normpath(value))


def _ShowVersion(_option, _opt, _value, _parser):
	id = '$Id: lddtree.py,v 1.6 2012/11/15 19:33:08 vapier Exp $'.split()
	print('%s-%s %s %s' % (id[1].split('.')[0], id[2], id[3], id[4]))
	sys.exit(0)


def _ActionShow(options, elf):
	"""Show the dependency tree for this ELF"""
	def _show(lib, depth):
		chain_libs.append(lib)
		fullpath = elf['libs'][lib]['path']
		if options.list:
			print(fullpath or lib)
		else:
			print('%s%s => %s' % ('    ' * depth, lib, fullpath))

		new_libs = []
		for lib in elf['libs'][lib]['needed']:
			if lib in chain_libs:
				if not options.list:
					print('%s%s => !!! circular loop !!!' % ('    ' * depth, lib))
				continue
			if options.all or not lib in shown_libs:
				shown_libs.add(lib)
				new_libs.append(lib)

		for lib in new_libs:
			_show(lib, depth + 1)
		chain_libs.pop()

	shown_libs = set(elf['needed'])
	chain_libs = []
	interp = elf['interp']
	if interp:
		shown_libs.add(os.path.basename(interp))
	if options.list:
		print(elf['path'])
		if not interp is None:
			print(interp)
	else:
		print('%s (interpreter => %s)' % (elf['path'], interp))
	for lib in elf['needed']:
		_show(lib, 1)


def _ActionCopy(options, elf):
	"""Copy the ELF and its dependencies to a destination tree"""
	def _copy(file):
		if file is None:
			return

		dest = options.dest + file
		if os.path.exists(dest):
			# See if they're the same file.
			ostat = os.stat(file)
			nstat = os.stat(dest)
			for field in ('mode', 'mtime', 'size'):
				if getattr(ostat, 'st_' + field) != \
				   getattr(nstat, 'st_' + field):
					break
			else:
				return

		if options.verbose:
			print('%s -> %s' % (file, dest))

		try:
			os.makedirs(os.path.dirname(dest))
		except OSError as e:
			if e.errno != os.errno.EEXIST:
				raise
		try:
			shutil.copy2(file, dest)
			return
		except IOError:
			os.unlink(dest)
		shutil.copy2(file, dest)

	_copy(elf['path'])
	_copy(elf['interp'])
	for lib in elf['libs']:
		_copy(elf['libs'][lib]['path'])


def main(argv):
	parser = optparse.OptionParser("""%prog [options] <ELFs>

Display ELF dependencies as a tree""")
	parser.add_option('-a', '--all',
		action='store_true', default=False,
		help=('Show all duplicated dependencies'))
	parser.add_option('-R', '--root',
		dest='root', default=os.environ.get('ROOT', ''), type='string',
		action='callback', callback=_NormalizePath,
		help=('Show all duplicated dependencies'))
	parser.add_option('--copy-to-tree',
		dest='dest', default=None, type='string',
		action='callback', callback=_NormalizePath,
		help=('Copy all files to the specified tree'))
	parser.add_option('-l', '--list',
		action='store_true', default=False,
		help=('Display output in a simple list (easy for copying)'))
	parser.add_option('-x', '--debug',
		action='store_true', default=False,
		help=('Run with debugging'))
	parser.add_option('-v', '--verbose',
		action='store_true', default=False,
		help=('Be verbose'))
	parser.add_option('-V', '--version',
		action='callback', callback=_ShowVersion,
		help=('Show version information'))
	(options, files) = parser.parse_args(argv)

	files.pop(0)
	if options.root != '/':
		options.root += '/'

	if options.debug:
		print('root =', options.root)
		if options.dest:
			print('dest =', options.dest)
	if not files:
		err('missing ELF files to scan')

	ldpaths = LoadLdpaths(options.root)
	if options.debug:
		print('ldpaths[conf] =', ldpaths['conf'])
		print('ldpaths[env]  =', ldpaths['env'])

	# Process all the files specified.
	ret = 0
	for file in files:
		try:
			elf = ParseELF(file, options.root, ldpaths)
		except (exceptions.ELFError, IOError) as e:
			ret = 1
			warn('%s: %s' % (file, e))
			continue
		if options.dest is None:
			_ActionShow(options, elf)
		else:
			_ActionCopy(options, elf)
	return ret


if __name__ == '__main__':
	sys.exit(main(sys.argv))
