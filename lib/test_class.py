import glob
import importlib
from inspect import getmembers
from inspect import isfunction
import os
import sys
import test_utils
import test_config
from test_config import ConfigNotFound
from test_result import TestResults

"""
This module implements the TestSet class which is responsible for discovering,
maintaining, and running a set of tests.

For each test we will store:
 - the function name, as declared in the Python module
 - the function itself, this is the code that will be executed for the test
 - the module name that the function came from

A typical workflow will be:
  1) Load tests into the class with add_from_directory
  2) Optionally prune the tests with methods like reduce_to_tags and set_script
  3) Run the tests which are left after pruning
"""


class SortType:
    def __init__(self):
        pass

    # Used for indicating the way discovered tests should be sorted
    MODULE_ALPHABETIC = 1


class TestSet():
    def __init__(self, copy_set=None):
        # tests is a list of test functions
        self._tests = list()
        if copy_set:
            self._tests = copy_set.tests

    @property
    def count(self):
        return len(self._tests)

    @property
    def tests(self):
        return self._tests

    def add_from_directory(self, directory, file_pat='test*.py', fn_name_pat='test_'):
        '''
        Adds all tests from a specified directory that match optional pattern
        to the current test set.
        :param directory: The directory to search for tests in
        :param file_pat: (Optional) File name glob
        :param fn_name_pat: (Optional) Indicates a file pattern which must
        match for a module file in the directory to be loaded
        :return: None
        '''

        logger = test_utils.get_logger()

        test_list = []

        # try to import each python file in the plugins directory
        sys.path.append(os.path.dirname(directory))
        for test_file in glob.glob1(directory, file_pat):

            module_name = os.path.basename(test_file).split('.')[0]

            # try to import the module by name
            try:
                outer = os.path.basename(os.path.normpath(directory))
                logger.debug("[+] Importing tests from file: {0}".format(
                             outer + '.' + module_name))

                module = importlib.import_module(outer + '.' + module_name)

            # if it fails, die
            except ImportError as e:
                import pdb; pdb.set_trace()
                logger.error("[-] Could not import test module '%s.%s'" %
                             (directory, module_name))
                logger.error("\tdetail: '%s'" % str(e))
                sys.exit(2)

            # otherwise we want to obtain a list of all functions in the module
            # and add them to our dictionary of tests
            else:
                functions_list = [
                    o for o in getmembers(module) if isfunction(o[1])
                ]

                for cur_func in functions_list:
                    # name of function is first element, function is second
                    fn_name = cur_func[0]

                    # only load functions that match the name pattern, this is
                    # to prevent loading decorators and utility functions
                    if str(fn_name).startswith(fn_name_pat):
                        try:
                            function = getattr(module, fn_name)
                        except AttributeError as e:
                            # we really shouldn't get here... just to be safe
                            logger.error("[-] Could not locate test function "
                                         "'%s' in module '%s.%s'" %
                                         (fn_name, directory, module_name))
                            sys.exit(2)
                        else:
                            new_test = dict()
                            new_test['name'] = fn_name
                            new_test['function'] = function
                            new_test['module'] = module_name
                            test_list.append(new_test)

        self._tests = _sort_tests(test_list, SortType.MODULE_ALPHABETIC)

    def reduce_to_tags(self, tags):
        '''
        Filter the current test set to one or more specified tags.  Will only
        keep a test if it has tags that match specified tags.
        :param tags: List of one or more tags to filter on
        :return: None
        '''
        # If tags is a non-empty list
        if isinstance(tags, list) and len(tags) > 0:
            new_tests = list()
            for test in self._tests:
                # Go through each test and check if it matches specified tags
                matched = False
                if hasattr(test['function'], 'tags'):
                    func_tags = test['function'].tags
                    for tag in tags:
                        if tag.strip() in func_tags:
                            matched = True
                            break
                # If it matches then add it to the new list
                if matched:
                    new_tests.append(test)
            self._tests = new_tests

    def run(self):
        '''
        Run all tests in this TestSet and return results
        :return: Tuple containing qualified test name and result
        '''
        results = list()
        for test in self._tests:
            cur_result = dict()

            test_name = "%s.%s" % (test['module'], test['name'])
            test_result = None

            fn = test['function']
            config = test_config.config

            # if a function takes config, pass it the data for it
            if hasattr(fn, 'takes_config') and config:
                try:
                    conf = config.get_config('modules.' + test_name)
                except ConfigNotFound:
                    logger = test_utils.get_logger()
                    logger.error("[-] Test {" + test_name + "} requires config "
                                 "but config could not be found.  Skipping...")
                else:
                    try:
                        test_result = fn(conf)
                    # catch anything that goes wrong with a test
                    except Exception as e:
                        logger = test_utils.get_logger()
                        logger.error("[-] Exception in test {" + test_name + "}: " + e.message)

            else:
                try:
                    test_result = fn()
                # catch anything that goes wrong with a test
                except Exception as e:
                    logger = test_utils.get_logger()
                    logger.error("[-] Exception in test {" + test_name + "}: " + e.message)

            # If the test actually ran...
            if test_result:
                # Name and result class are added
                cur_result = dict()
                cur_result['name'] = test_name
                # here the result can either be an individual result or a group
                # result, in either case add it to the results list
                cur_result['result'] = test_result
                results.append(cur_result)

        return TestResults(results)

    def set_script(self, script_file):
        '''
        This method takes a script file, and sets the test set to run specified
        tests in the order listed in the script.
        :param script_file: File to use for script
        :return: -
        '''

        script_lines = []
        new_test_set = []
        logger = test_utils.get_logger()

        try:
            script_f = open(script_file, 'r')
            script_lines = script_f.readlines()
            script_f.close()

        except IOError:
            logger.error("[-] Unable to open script file { " + script_file + " }")
            sys.exit(2)

        else:
            for line in script_lines:
                # for each line in the script, find the appropriate test
                # and add it
                test = self._find_test_by_can_name(line.strip())
                if not test:
                    logger.error("[-] Unable to find test: { " +
                                 line.strip('\n') + " }")
                    sys.exit(2)
                new_test_set.append(test)

        logger.info("[+] Loaded script { " + script_file + " }")
        self._tests = new_test_set

    def _find_test_by_can_name(self, module_name):
        '''
        Find a test by it's canonical name: module name + '.' + test name
        :return: The test dictionary for specified test
        '''

        logger = test_utils.get_logger()

        # module name is the part before the . , test name is the part after
        module_ids = module_name.split('.')

        # if we don't have a well-formed canonical name, don't try to find it
        if len(module_ids) != 2:
            logger.error("[-] Malformed script line: { " + line + " }")
            return None

        for test in self._tests:
            if(test['module'] == module_ids[0] and
                    test['name'] == module_ids[1]):
                return test
        return None


def _sort_tests(sort_list, sort_type):
    sorted_list = []

    # other sort types might be supported in the future...
    if sort_type == SortType.MODULE_ALPHABETIC:
        # sorting module alphabetic sorts according to module name first, then
        # function name
        sorted_list = sorted(sort_list,
                             key=lambda test_name: test_name['module'] +
                             '.' + test_name['name'])

    return sorted_list


def tag(*tags):
    """Decorator to add tags to test functions.

    Example:
        @testutils.tag("kernel")
        def test_xyz(self):
    """
    def decorate(f):
        if not hasattr(f, "tags"):
            f.tags = []
        f.tags += tags
        return f
    return decorate


def explanation(exp):
    """
    Decorator to add an explanation for why a test is important from a
    security perspective
    :param exp: String explanation of the test
    :return: Function which contains the "explanation" attribute
    """
    def decorate(f):
        f.explanation = exp
        return f
    return decorate


def group_test(func):
    """Decorator to indicate that function is a group test

    Example:
        @group_test
        def test_xyz(self):
    """
    # Just having the attribute indicates that it is a group test
    func.is_group = None
    return func


def takes_config(func):
    """Decorator to indicate that function takes a config dictionary

    Example:
        @takes_config
        def test_xyz(self):
    """
    # Just having the attribute indicates that it takes config, no value needed
    func.takes_config = None
    return func