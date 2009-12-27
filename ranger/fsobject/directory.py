from ranger.fsobject import BAD_INFO, File, FileSystemObject
from ranger.shared import SettingsAware
from ranger.ext.accumulator import Accumulator
from ranger import log
import ranger.fsobject

def sort_by_basename(path):
	"""returns path.basename (for sorting)"""
	return path.basename

def sort_by_directory(path):
	"""returns 0 if path is a directory, otherwise 1 (for sorting)"""
	return 1 - int( isinstance( path, Directory ) )

class NoDirectoryGiven(Exception):
	pass

class Directory(FileSystemObject, Accumulator, SettingsAware):
	enterable = False
	load_generator = None
	loading = False

	filenames = None
	files = None
	filter = None
	marked_items = None
	scroll_begin = 0
	scroll_offset = 0

	old_show_hidden = None
	old_directories_first = None
	old_reverse = None
	old_sort = None
	old_filter = None

	sort_dict = {
		'basename': sort_by_basename,
		'size': lambda path: path.size,
		'mtime': lambda path: -(path.stat and path.stat.st_mtime or 1),
		'type': lambda path: path.mimetype,
	}

	def __init__(self, path):
		from os.path import isfile

		if isfile(path):
			raise NoDirectoryGiven()

		Accumulator.__init__(self)
		FileSystemObject.__init__(self, path)

		self.marked_items = set()

		# to find out if something has changed:
		self.old_show_hidden = self.settings.show_hidden
		self.old_directories_first = self.settings.directories_first
		self.old_sort = self.settings.sort
		self.old_filter = self.filter
		self.old_reverse = self.settings.reverse
	
	def get_list(self):
		return self.files
	
	def mark_item(self, item, val):
		item._mark(val)
		if val:
			if item in self.files:
				self.marked_items.add(item)
		else:
			if item in self.marked_items:
				self.marked_items.remove(item)

	def toggle_mark(self, item):
		self.mark_item(item, not item.marked)

	def toggle_all_marks(self):
		for item in self.files:
			self.toggle_mark(item)
	
	def mark_all(self, val):
		for item in self.files:
			self.mark_item(item, val)

		if not val:
			self.marked_items.clear()
			self._clear_marked_items()
	
	def _gc_marked_items(self):
		for item in self.marked_items.copy():
			if item.path not in self.filenames:
				self.marked_items.remove(item)
	
	def _clear_marked_items(self):
		for item in self.marked_items:
			item._mark(False)
		self.marked_items.clear()

	def get_selection(self):
		"""READ ONLY"""
		self._gc_marked_items()
		if self.marked_items:
			return set(self.marked_items)
		elif self.pointed_obj:
			return set([self.pointed_obj])
		else:
			return set()
	
	def load_bit_by_bit(self):
		"""
		Returns a generator which load a part of the directory
		in each iteration.
		"""

#		log("generating loader for " + self.path + "(" + str(id(self)) + ")")
		from os.path import join, isdir, basename
		from os import listdir

		self.loading = True
		self.load_if_outdated()

		if self.exists and self.runnable:
			yield
			filenames = []
			for fname in listdir(self.path):
				if not self.settings.show_hidden and fname[0] == '.':
					continue
				if isinstance(self.filter, str) and self.filter \
						and self.filter not in fname:
					continue
				filenames.append(join(self.path, fname))
			yield

			marked_paths = set(map( \
					lambda obj: obj.path, self.marked_items))

			files = []
			for name in filenames:
				if isdir(name):
					try:
						item = self.fm.env.get_directory(name)
					except:
						item = Directory(name)
				else:
					item = File(name)
				item.load_if_outdated()
				files.append(item)
				yield

			self.scroll_offset = 0
			self.filenames = filenames
			self.infostring = ' %d' % len(self.filenames) # update the infostring
			self.files = files

			self._clear_marked_items()
			for item in self.files:
				if item.path in marked_paths:
					self.mark_item(item, True)
				else:
					self.mark_item(item, False)

			self.old_directories_first = None
			self.sort()

			if len(self.files) > 0:
				if self.pointed_obj is not None:
					self.sync_index()
				else:
					self.move(absolute=0)
		else:
			self.filenames = None
			self.files = None
			self.infostring = BAD_INFO

		self.content_loaded = True
		self.loading = False

	def unload(self):
		self.loading = False
		self.load_generator = None

	def load_content(self, schedule=None):
		"""
		Loads the contents of the directory. Use this sparingly since
		it takes rather long.
		"""

		if not self.loading:
			self.load_once()
			
			if schedule is None:
				schedule = self.size > 30

			if self.load_generator is None:
				self.load_generator = self.load_bit_by_bit()

				if schedule and self.fm:
					self.fm.loader.add(self)
				else:
					for _ in self.load_generator:
						pass
					self.load_generator = None

			elif not schedule or not self.fm:
				for _ in self.load_generator:
					pass
				self.load_generator = None


	def sort(self):
		"""Sort the containing files"""
		if self.files is None:
			return

		old_pointed_obj = self.pointed_obj
		try:
			sort_func = self.sort_dict[self.settings.sort]
		except:
			sort_func = sort_by_basename
		self.files.sort(key = sort_func)

		if self.settings.reverse:
			self.files.reverse()

		if self.settings.directories_first:
			self.files.sort(key = sort_by_directory)

		if self.pointer is not None:
			self.move_to_obj(old_pointed_obj)
		else:
			self.correct_pointer()

		self.old_directories_first = self.settings.directories_first
		self.old_sort = self.settings.sort
		self.old_reverse = self.settings.reverse
	
	def sort_if_outdated(self):
		"""Sort the containing files if they are outdated"""
		if self.old_directories_first != self.settings.directories_first \
				or self.old_sort != self.settings.sort \
				or self.old_reverse != self.settings.reverse:
			self.sort()

	def move_to_obj(self, arg):
		try:
			arg = arg.path
		except:
			pass
		self.load_content_once(schedule=False)
		if self.empty():
			return

		Accumulator.move_to_obj(self, arg, attr='path')

	def search_fnc(self, fnc, forward=True):
		if not hasattr(fnc, '__call__'):
			return False

		length = len(self)

		if forward:
			generator = ((self.pointer + (x + 1)) % length \
					for x in range(length-1))
		else:
			generator = ((self.pointer - (x + 1)) % length \
					for x in range(length-1))

		for i in generator:
			_file = self.files[i]
			if fnc(_file):
				self.pointer = i
				self.pointed_obj = _file
				self.correct_pointer()
				return True
		return False

	def correct_pointer(self):
		"""Make sure the pointer is in the valid range"""
		Accumulator.correct_pointer(self)

		try:
			if self == self.fm.env.pwd:
				self.fm.env.cf = self.pointed_obj
		except:
			pass
		
	def load_content_once(self, *a, **k):
		"""Load the contents of the directory if not done yet"""
		if not self.content_loaded:
			self.load_content(*a, **k)
			return True
		return False

	def load_content_if_outdated(self, *a, **k):
		"""
		Load the contents of the directory if it's
		outdated or not done yet
		"""

		if self.load_content_once(*a, **k): return True

		if self.old_show_hidden != self.settings.show_hidden or \
				self.old_filter != self.filter:
			self.old_filter = self.filter
			self.old_show_hidden = self.settings.show_hidden
			self.load_content(*a, **k)
			return True

		import os
		try:
			real_mtime = os.lstat(self.path).st_mtime
		except OSError:
			real_mtime = None
		if self.stat:
			cached_mtime = self.stat.st_mtime
		else:
			cached_mtime = 0

		if real_mtime != cached_mtime:
			self.load_content(*a, **k)
			return True
		return False

	def empty(self):
		"""Is the directory empty?"""
		return self.files is None or len(self.files) == 0

	def __nonzero__(self):
		"""Always True"""
		return True

	def __len__(self):
		"""The number of containing files"""
		if not self.accessible: raise ranger.fsobject.NotLoadedYet()
		return len(self.files)
	
	def __getitem__(self, key):
		"""Get the file by its index"""
		if not self.accessible: raise ranger.fsobject.NotLoadedYet()
		return self.files[key]

	def __eq__(self, other):
		"""Check for equality of the directories paths"""
		return isinstance(other, Directory) and self.path == other.path

	def __neq__(self, other):
		"""Check for inequality of the directories paths"""
		return not self.__eq__(other)
	
	def __hash__(self):
		return hash(self.path)
