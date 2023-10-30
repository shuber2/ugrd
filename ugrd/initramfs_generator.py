
__author__ = "desultory"
__version__ = "0.6.2"

from tomllib import load
from pathlib import Path
from subprocess import run

from ugrd.zen_custom import loggify
from ugrd.initramfs_dict import InitramfsConfigDict


@loggify
class InitramfsGenerator:
    def __init__(self, config='/etc/ugrd/config.toml', *args, **kwargs):
        self.config_filename = config
        self.build_pre = [self.generate_structure]
        self.build_tasks = [self.deploy_dependencies]
        self.config_dict = InitramfsConfigDict(logger=self.logger)

        # init_pre and init_final are run as part of generate_initramfs_main
        self.init_types = ['init_main', 'init_late', 'init_mount']

        self.load_config()
        self.config_dict.verify_deps()
        self.config_dict.verify_mask()

    def load_config(self):
        """
        Loads the config from the specified toml file
        """
        with open(self.config_filename, 'rb') as config_file:
            self.logger.info("Loading config file: %s" % config_file.name)
            raw_config = load(config_file)

        # Process into the config dict, it should handle parsing
        for config, value in raw_config.items():
            self.logger.debug("Processing config key: %s" % config)
            self.config_dict[config] = value

        self.logger.debug("Loaded config: %s" % self.config_dict)

        for parameter in ['build_dir', 'out_dir', 'clean']:
            dict_value = self.config_dict[parameter]
            if dict_value is not None:
                setattr(self, parameter, dict_value)
            else:
                raise KeyError("Required parameter '%s' not found in config" % parameter)

    def build_structure(self):
        """
        builds the initramfs structure
        """
        # If clean is set, clear the target build dir
        if self.clean:
            from shutil import rmtree
            from os.path import isdir
            # If the build dir is present, clean it, otherwise log and continue
            if isdir(self.build_dir):
                self.logger.warning("Cleaning build dir: %s" % self.build_dir)
                rmtree(self.build_dir)
            else:
                self.logger.info("Build dir is not present, not cleaning: %s" % self.build_dir)
        else:
            self.logger.debug("Not cleaning build dir: %s" % self.build_dir)

        # Run pre-build tasks, by default just calls 'generate_structure'
        self.logger.info("Running pre build tasks")
        self.logger.debug(self.build_pre)
        for task in self.build_pre:
            task()

        # Run custom pre-build tasks imported from modules
        if build_pre := self.config_dict['imports'].get('build_pre'):
            self.logger.info("Running custom pre build tasks")
            self.logger.debug(build_pre)
            for task in build_pre:
                task(self)

        # Run all build tasks, by default just calls 'deploy_dependencies'
        self.logger.info("Running build tasks")
        self.logger.debug(self.build_tasks)
        for task in self.build_tasks:
            task()

        # Run custom build tasks imported from modules
        if build_tasks := self.config_dict['imports'].get('build_tasks'):
            self.logger.info("Running custom build tasks")
            self.logger.debug(build_tasks)
            for task in build_tasks:
                task(self)

    def generate_init_main(self):
        """
        Generates the main init file.
        """
        out = list()

        for init_type in self.init_types:
            out += self._run_hook(init_type)

        return out

    def _run_hook(self, level):
        """
        Runs an init hook
        """
        self.logger.info("Running init level: %s" % level)
        out = ['\n\n# Begin %s' % level]
        for func in self.config_dict['imports'].get(level):
            self.logger.info("Running init generator function: %s" % func.__name__)
            if function_output := func(self):
                if isinstance(function_output, str):
                    self.logger.debug("[%s] Function returned string: %s" % (func.__name__, function_output))
                    out += [function_output]
                else:
                    self.logger.debug("[%s] Function returned output: %s" % (func.__name__, function_output))
                    out.extend(function_output)
            else:
                self.logger.warning("Function returned no output: %s" % func.__name__)
        return out

    def generate_init(self):
        """
        Generates the init file
        """
        self.logger.info("Running init generator functions")

        init = [self.config_dict['shebang']]

        init += ["# Generated by initramfs_generator.py v%s" % __version__]

        init += self._run_hook('init_pre')
        init += self._run_hook('custom_init') if self.config_dict['imports'].get('custom_init') else self.generate_init_main()
        init += self._run_hook('init_final')

        init += ["\n\n# END INIT"]

        self._write('init', init, 0o755)

        self.logger.debug("Final config: %s" % self.config_dict)

    def generate_structure(self):
        """
        Generates the initramfs directory structure
        """
        from os.path import isdir

        if not isdir(self.build_dir):
            self._mkdir(self.build_dir)

        for subdir in set(self.config_dict['paths']):
            subdir_path = Path(subdir)
            subdir_relative_path = subdir_path.relative_to(subdir_path.anchor)
            target_dir = self.build_dir / subdir_relative_path

            self._mkdir(target_dir)

    def pack(self):
        """
        Packs the initramfs based on self.config_dict['imports']['pack']
        """
        if pack_funcs := self.config_dict['imports'].get('pack'):
            self.logger.info("Running custom pack functions")
            self.logger.debug(pack_funcs)
            for func in pack_funcs:
                func(self)
        else:
            self.logger.warning("No pack functions specified, the final build is present in: %s" % self.build_dir)

    def _mkdir(self, path):
        """
        Creates a directory, chowns it as self.config_dict['_file_owner_uid']
        """
        from os.path import isdir
        from os import mkdir

        self.logger.debug("Creating directory for: %s" % path)

        if path.is_dir():
            path_dir = path.parent
            self.logger.debug("Directory path: %s" % path_dir)
        else:
            path_dir = path

        if not isdir(path_dir.parent):
            self.logger.debug("Parent directory does not exist: %s" % path_dir.parent)
            self._mkdir(path_dir.parent)

        if not isdir(path_dir):
            mkdir(path)
            self.logger.info("Created directory: %s" % path)
        else:
            self.logger.debug("Directory already exists: %s" % path_dir)

        self._chown(path_dir)

    def _chown(self, path):
        """
        Chowns a file or directory as self.config_dict['_file_owner_uid']
        """
        from os import chown

        if path.owner() == self.config_dict['_file_owner_uid'] and path.group() == self.config_dict['_file_owner_uid']:
            self.logger.debug("File '%s' already owned by: %s" % (path, self.config_dict['_file_owner_uid']))
            return

        chown(path, self.config_dict['_file_owner_uid'], self.config_dict['_file_owner_uid'])
        if path.is_dir():
            self.logger.debug("[%s] Set directory owner: %s" % (path, self.config_dict['_file_owner_uid']))
        else:
            self.logger.debug("[%s] Set file owner: %s" % (path, self.config_dict['_file_owner_uid']))

    def _write(self, file_name, contents, chmod_mask=0o644, in_build_dir=True):
        """
        Writes a file and owns it as self.config_dict['_file_owner_uid']
        Sets the passed chmod
        """
        from os import chmod

        if in_build_dir:
            if file_name.startswith('/'):
                file_path = self.config_dict['build_dir'] / Path(file_name).relative_to('/')
            else:
                file_path = self.config_dict['build_dir'] / file_name
        else:
            file_path = Path(file_name)

        self.logger.debug("[%s] Writing contents: %s: " % (file_path, contents))
        with open(file_path, 'w') as file:
            file.writelines("\n".join(contents))

        self.logger.info("Wrote file: %s" % file_path)
        chmod(file_path, chmod_mask)
        self.logger.debug("[%s] Set file permissions: %s" % (file_path, chmod_mask))

        self._chown(file_path)

    def _copy(self, source, dest=None, in_build_dir=True):
        """
        Copies a file, chowns it as self.config_dict['_file_owner_uid']
        """
        from shutil import copy2

        if not isinstance(source, Path):
            source = Path(source)

        if not dest:
            self.logger.debug("No destination specified, using source: %s" % source)
            dest = source
        elif not isinstance(dest, Path):
            dest = Path(dest)

        if in_build_dir:
            if dest.is_absolute():
                dest_path = self.config_dict['build_dir'] / Path(dest).relative_to('/')
            else:
                dest_path = self.config_dict['build_dir'] / dest
        else:
            dest_path = Path(dest)

        if not dest_path.parent.is_dir():
            self.logger.debug("Parent directory for '%s' does not exist: %s" % (dest_path.name, dest.parent))
            self._mkdir(dest_path.parent)

        if dest_path.is_file():
            self.logger.warning("File already exists: %s" % dest_path)
        self.logger.info("Copying '%s' to '%s'" % (source, dest_path))
        copy2(source, dest_path)

        self._chown(dest_path)

    def _run(self, args):
        """
        Runs a command, returns the object
        """
        self.logger.debug("Running command: %s" % args)
        cmd = run(args, capture_output=True)
        if cmd.returncode != 0:
            self.logger.error("Failed to run command: %s" % cmd.args)
            self.logger.error("Command output: %s" % cmd.stdout.decode())
            self.logger.error("Command error: %s" % cmd.stderr.decode())
            raise RuntimeError("Failed to run command: %s" % cmd.args)

        return cmd

    def deploy_dependencies(self):
        """
        Copies all required dependencies
        should be used after generate_structure
        """
        for dependency in self.config_dict['dependencies']:
            self.logger.debug("Deploying dependency: %s" % dependency)
            self._copy(dependency)
