import logging
import os
import re
import subprocess
import sys

from . import logging_colored

_logger = logging.getLogger("pre-commit-vauxoo")

re_export = re.compile(
    r"^(?P<export>export|EXPORT)( )+"
    + r"(?P<variable>[\w]*)[ ]*[\=][ ]*[\"\']{0,1}"
    + r"(?P<value>[\w\.\-\_/\$\{\}\:,\(\)\#\* ]*)[\"\']{0,1}",
    re.M,
)


def get_repo():
    repo_root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).decode(sys.stdout.encoding).strip()
    repo_root = os.path.abspath(os.path.realpath(repo_root))
    return repo_root


def get_files(path):
    ls_files = subprocess.check_output(["git", "ls-files", "--", path]).decode(sys.stdout.encoding).strip()
    ls_files = ls_files.splitlines()
    return ls_files


def copy_cfg_files(precommit_config_dir, repo_dirname, overwrite, exclude_lint, disable_pylint_checks):
    exclude_regex = ""
    if exclude_lint:
        exclude_regex = "(%s)|" % "|".join(
            [
                re.escape(exclude_path.strip())
                for exclude_path in exclude_lint.split(",")
                if exclude_path and exclude_path.strip()
            ]
        )
    _logger.info("Copying configuration files 'cp -rnT %s/ %s/", precommit_config_dir, repo_dirname)
    for fname in os.listdir(precommit_config_dir):
        if not fname.startswith(".") and fname != "pyproject.toml":
            # all configuration files are hidden
            continue
        src = os.path.join(precommit_config_dir, fname)
        if not os.path.isfile(src):
            # if it is not a file skip
            continue
        dst = os.path.join(repo_dirname, fname)
        if not overwrite and os.path.isfile(dst):
            # Use the custom files defined in the repo
            _logger.warning("Use custom file %s", dst)
            continue
        with open(src, "r") as fsrc, open(dst, "w") as fdst:
            for line in fsrc:
                if exclude_lint and fname.startswith(".pre-commit-config") and "# EXCLUDE_LINT" in line:
                    _logger.info("Apply EXCLUDE_LINT=%s to %s", exclude_lint, dst)
                    line = "    %s\n" % exclude_regex
                if disable_pylint_checks and fname.startswith(".pre-commit-config") and "--disable=R0000" in line:
                    line = line.replace("R0000", disable_pylint_checks)
                fdst.write(line)


def envfile2envdict(repo_dirname, source_file="variables.sh"):
    """Simulate load the Vauxoo standard file 'source variables.sh' command in python
    return dictionary {environment_variable: value}
    """
    envdict = {}
    source_file_path = os.path.join(repo_dirname, source_file)
    if not os.path.isfile(source_file_path):
        _logger.info("Skip 'source %s' file not found", source_file_path)
        return envdict
    with open(source_file_path) as f_source_file:
        _logger.info("Running 'source %s'", source_file_path)
        for line in f_source_file:
            line_match = re_export.match(line)
            if not line_match:
                continue
            envdict.update({line_match["variable"]: line_match["value"]})
    return envdict


def subprocess_call(command, *args, **kwargs):
    cmd_str = ' '.join(command)
    _logger.debug("Command executed: %s", cmd_str)
    return subprocess.call(command, *args, **kwargs)


def main(argv=None, do_exit=True):
    repo_dirname = get_repo()
    cwd = os.path.abspath(os.path.realpath(os.getcwd()))

    envdict = envfile2envdict(repo_dirname)
    os.environ.update(envdict)

    # Get the configuration files but you can use custom ones so set "0"
    overwrite = os.environ.get("PRECOMMIT_OVERWRITE_CONFIG_FILES", "1") == "1"
    # Exclude paths to lint
    exclude_lint = os.environ.get("EXCLUDE_LINT", "")
    # Disable pylint checks
    disable_pylint_checks = os.environ.get("DISABLE_PYLINT_CHECKS", "")
    # Enable .pre-commit-config-autofix.yaml configuration file
    enable_auto_fix = os.environ.get("PRECOMMIT_AUTOFIX", "") == "1"
    root_dir = os.path.dirname(os.path.abspath(__file__))
    precommit_config_dir = os.path.join(root_dir, "cfg")

    copy_cfg_files(
        precommit_config_dir,
        repo_dirname,
        overwrite,
        exclude_lint,
        disable_pylint_checks,
    )

    _logger.info("Installing pre-commit hooks")
    cmd = ["pre-commit", "install-hooks", "--color=always"]
    pre_commit_cfg_mandatory = os.path.join(repo_dirname, ".pre-commit-config.yaml")
    pre_commit_cfg_optional = os.path.join(repo_dirname, ".pre-commit-config-optional.yaml")
    pre_commit_cfg_autofix = os.path.join(repo_dirname, ".pre-commit-config-autofix.yaml")
    subprocess_call(cmd + ["-c", pre_commit_cfg_mandatory])
    subprocess_call(cmd + ["-c", pre_commit_cfg_optional])
    if enable_auto_fix:
        subprocess_call(cmd + ["-c", pre_commit_cfg_autofix])

    status = 0
    cmd = ["pre-commit", "run", "--color=always"]
    if cwd != repo_dirname:
        cwd_short = os.path.relpath(cwd, repo_dirname)
        _logger.warning("Running only for sub-path '%s'", cwd_short)
        files = get_files(cwd)
        if not files:
            raise UserWarning("Not files detected in current path %s" % cwd_short)
        cmd.extend(["--files"] + files)
    else:
        cmd.append("--all")
    all_status = {}

    if enable_auto_fix:
        _logger.info("%s AUTOFIX CHECKS %s", "-" * 25, "-" * 25)
        _logger.info("Running autofix checks (affect status build but you can autofix them locally)")
        autofix_status = subprocess_call(cmd + ["-c", pre_commit_cfg_autofix])
        status += autofix_status
        test_name = 'Autofix checks'
        all_status[test_name] = {'status': autofix_status}
        if autofix_status != 0:
            _logger.error("%s reformatted", test_name)
            all_status[test_name]['level'] = logging.ERROR
            all_status[test_name]['status_msg'] = "Reformatted"
        else:
            _logger.info("%s passed!", test_name)
            all_status[test_name]['level'] = logging.INFO
            all_status[test_name]['status_msg'] = "Passed"
        _logger.info("-" * 66)

    _logger.info("%s MANDATORY CHECKS %s", "*" * 25, "*" * 25)
    _logger.info("Running mandatory checks (affect status build)")
    mandatory_status = subprocess_call(cmd + ["-c", pre_commit_cfg_mandatory])
    status += mandatory_status
    test_name = 'Mandatory checks'
    all_status[test_name] = {'status': mandatory_status}
    if status != 0:
        _logger.error("%s failed", test_name)
        all_status[test_name]['level'] = logging.ERROR
        all_status[test_name]['status_msg'] = "Failed"
    else:
        _logger.info("%s passed!", test_name)
        all_status[test_name]['level'] = logging.INFO
        all_status[test_name]['status_msg'] = "Passed"

    _logger.info("*" * 68)
    _logger.info("%s OPTIONAL CHECKS %s", "~" * 25, "~" * 25)
    _logger.info("Running optional checks (does not affect status build)")
    status_optional = subprocess_call(cmd + ["-c", os.path.join(repo_dirname, pre_commit_cfg_optional)])
    test_name = 'Optional checks'
    all_status[test_name] = {'status': status_optional}
    if status_optional != 0:
        _logger.warning("Optional checks failed")
        all_status[test_name]['level'] = logging.WARNING
        all_status[test_name]['status_msg'] = "Failed"
    else:
        _logger.info("Optional checks passed!")
        all_status[test_name]['level'] = logging.INFO
        all_status[test_name]['status_msg'] = "Passed"
    _logger.info("~" * 67)

    print_summary(all_status)
    if do_exit:
        sys.exit(0 if status == 0 else 1)


def print_summary(all_status):
    summary_msg = ["+" + "=" * 39]
    summary_msg.append("|  Tests summary:")
    summary_msg.append("|" + "-" * 39)
    for test_name, test_result in all_status.items():
        outcome = (
            logging_colored.colorized_msg(test_result['status_msg'], test_result['level'])
            if test_result['status'] != 0
            else logging_colored.colorized_msg(test_result['status_msg'], test_result['level'])
        )
        summary_msg.append("| {:<28}{}".format(test_name, outcome))
    summary_msg.append("+" + "=" * 39)
    _logger.info("Tests summary\n%s", '\n'.join(summary_msg))


if __name__ == "__main__":
    main()
