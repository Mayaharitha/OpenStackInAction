#   Copyright 2012-2013 OpenStack Foundation
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.
#

"""Command-line interface to the OpenStack APIs"""

import getpass
import logging
import sys
import traceback

from cliff import app
from cliff import command
from cliff import complete
from cliff import help

import openstackclient
from openstackclient.common import clientmanager
from openstackclient.common import commandmanager
from openstackclient.common import exceptions as exc
from openstackclient.common import timing
from openstackclient.common import utils


DEFAULT_DOMAIN = 'default'


class OpenStackShell(app.App):

    CONSOLE_MESSAGE_FORMAT = '%(levelname)s: %(name)s %(message)s'

    log = logging.getLogger(__name__)
    timing_data = []

    def __init__(self):
        # Patch command.Command to add a default auth_required = True
        command.Command.auth_required = True
        command.Command.best_effort = False
        # But not help
        help.HelpCommand.auth_required = False
        complete.CompleteCommand.best_effort = True

        super(OpenStackShell, self).__init__(
            description=__doc__.strip(),
            version=openstackclient.__version__,
            command_manager=commandmanager.CommandManager('openstack.cli'))

        self.api_version = {}

        # Until we have command line arguments parsed, dump any stack traces
        self.dump_stack_trace = True

        # This is instantiated in initialize_app() only when using
        # password flow auth
        self.auth_client = None

        # Assume TLS host certificate verification is enabled
        self.verify = True

        # NOTE(dtroyer): This hack changes the help action that Cliff
        #                automatically adds to the parser so we can defer
        #                its execution until after the api-versioned commands
        #                have been loaded.  There doesn't seem to be a
        #                way to edit/remove anything from an existing parser.

        # Replace the cliff-added help.HelpAction to defer its execution
        self.DeferredHelpAction = None
        for a in self.parser._actions:
            if type(a) == help.HelpAction:
                # Found it, save and replace it
                self.DeferredHelpAction = a

                # These steps are argparse-implementation-dependent
                self.parser._actions.remove(a)
                if self.parser._option_string_actions['-h']:
                    del self.parser._option_string_actions['-h']
                if self.parser._option_string_actions['--help']:
                    del self.parser._option_string_actions['--help']

                # Make a new help option to just set a flag
                self.parser.add_argument(
                    '-h', '--help',
                    action='store_true',
                    dest='deferred_help',
                    default=False,
                    help="Show this help message and exit",
                )

    def configure_logging(self):
        """Configure logging for the app

        Cliff sets some defaults we don't want so re-work it a bit
        """

        if self.options.debug:
            # --debug forces verbose_level 3
            # Set this here so cliff.app.configure_logging() can work
            self.options.verbose_level = 3

        super(OpenStackShell, self).configure_logging()
        root_logger = logging.getLogger('')

        # Requests logs some stuff at INFO that we don't want
        # unless we have DEBUG
        requests_log = logging.getLogger("requests")
        requests_log.setLevel(logging.ERROR)

        # Other modules we don't want DEBUG output for so
        # don't reset them below
        iso8601_log = logging.getLogger("iso8601")
        iso8601_log.setLevel(logging.ERROR)

        # Set logging to the requested level
        self.dump_stack_trace = False
        if self.options.verbose_level == 0:
            # --quiet
            root_logger.setLevel(logging.ERROR)
        elif self.options.verbose_level == 1:
            # This is the default case, no --debug, --verbose or --quiet
            root_logger.setLevel(logging.WARNING)
        elif self.options.verbose_level == 2:
            # One --verbose
            root_logger.setLevel(logging.INFO)
        elif self.options.verbose_level >= 3:
            # Two or more --verbose
            root_logger.setLevel(logging.DEBUG)
            requests_log.setLevel(logging.DEBUG)

        if self.options.debug:
            # --debug forces traceback
            self.dump_stack_trace = True

    def run(self, argv):
        try:
            return super(OpenStackShell, self).run(argv)
        except Exception as e:
            if not logging.getLogger('').handlers:
                logging.basicConfig()
            if self.dump_stack_trace:
                self.log.error(traceback.format_exc(e))
            else:
                self.log.error('Exception raised: ' + str(e))
            return 1

    def build_option_parser(self, description, version):
        parser = super(OpenStackShell, self).build_option_parser(
            description,
            version)

        # service token auth argument
        parser.add_argument(
            '--os-url',
            metavar='<url>',
            default=utils.env('OS_URL'),
            help='Defaults to env[OS_URL]')
        # Global arguments
        parser.add_argument(
            '--os-region-name',
            metavar='<auth-region-name>',
            default=utils.env('OS_REGION_NAME'),
            help='Authentication region name (Env: OS_REGION_NAME)')
        parser.add_argument(
            '--os-cacert',
            metavar='<ca-bundle-file>',
            default=utils.env('OS_CACERT'),
            help='CA certificate bundle file (Env: OS_CACERT)')
        verify_group = parser.add_mutually_exclusive_group()
        verify_group.add_argument(
            '--verify',
            action='store_true',
            help='Verify server certificate (default)',
        )
        verify_group.add_argument(
            '--insecure',
            action='store_true',
            help='Disable server certificate verification',
        )
        parser.add_argument(
            '--os-default-domain',
            metavar='<auth-domain>',
            default=utils.env(
                'OS_DEFAULT_DOMAIN',
                default=DEFAULT_DOMAIN),
            help='Default domain ID, default=' +
                 DEFAULT_DOMAIN +
                 ' (Env: OS_DEFAULT_DOMAIN)')
        parser.add_argument(
            '--timing',
            default=False,
            action='store_true',
            help="Print API call timing info",
        )

        return clientmanager.build_plugin_option_parser(parser)

    def authenticate_user(self):
        """Verify the required authentication credentials are present"""

        self.log.debug("validating authentication options")

        # Assuming all auth plugins will be named in the same fashion,
        # ie vXpluginName
        if (not self.options.os_url and
           self.options.os_auth_plugin.startswith('v') and
           self.options.os_auth_plugin[1] !=
           self.options.os_identity_api_version[0]):
            raise exc.CommandError(
                "Auth plugin %s not compatible"
                " with requested API version" % self.options.os_auth_plugin
            )
        # TODO(mhu) All these checks should be exposed at the plugin level
        # or just dropped altogether, as the client instantiation will fail
        # anyway
        if self.options.os_url and not self.options.os_token:
            # service token needed
            raise exc.CommandError(
                "You must provide a service token via"
                " either --os-token or env[OS_TOKEN]")

        if (self.options.os_auth_plugin.endswith('token') and
           (self.options.os_token or self.options.os_auth_url)):
            # Token flow auth takes priority
            if not self.options.os_token:
                raise exc.CommandError(
                    "You must provide a token via"
                    " either --os-token or env[OS_TOKEN]")

            if not self.options.os_auth_url:
                raise exc.CommandError(
                    "You must provide a service URL via"
                    " either --os-auth-url or env[OS_AUTH_URL]")

        if (not self.options.os_url and
           not self.options.os_auth_plugin.endswith('token')):
            # Validate password flow auth
            if not self.options.os_username:
                raise exc.CommandError(
                    "You must provide a username via"
                    " either --os-username or env[OS_USERNAME]")

            if not self.options.os_password:
                # No password, if we've got a tty, try prompting for it
                if hasattr(sys.stdin, 'isatty') and sys.stdin.isatty():
                    # Check for Ctl-D
                    try:
                        self.options.os_password = getpass.getpass()
                    except EOFError:
                        pass
                # No password because we did't have a tty or the
                # user Ctl-D when prompted?
                if not self.options.os_password:
                    raise exc.CommandError(
                        "You must provide a password via"
                        " either --os-password, or env[OS_PASSWORD], "
                        " or prompted response")

            if not ((self.options.os_project_id
                    or self.options.os_project_name) or
                    (self.options.os_domain_id
                    or self.options.os_domain_name) or
                    self.options.os_trust_id):
                if self.options.os_auth_plugin.endswith('password'):
                    raise exc.CommandError(
                        "You must provide authentication scope as a project "
                        "or a domain via --os-project-id "
                        "or env[OS_PROJECT_ID], "
                        "--os-project-name or env[OS_PROJECT_NAME], "
                        "--os-domain-id or env[OS_DOMAIN_ID], or"
                        "--os-domain-name or env[OS_DOMAIN_NAME], or "
                        "--os-trust-id or env[OS_TRUST_ID].")

            if not self.options.os_auth_url:
                raise exc.CommandError(
                    "You must provide an auth url via"
                    " either --os-auth-url or via env[OS_AUTH_URL]")

            if (self.options.os_trust_id and
               self.options.os_identity_api_version != '3'):
                raise exc.CommandError(
                    "Trusts can only be used with Identity API v3")

            if (self.options.os_trust_id and
               ((self.options.os_project_id
                 or self.options.os_project_name) or
                (self.options.os_domain_id
                 or self.options.os_domain_name))):
                raise exc.CommandError(
                    "Authentication cannot be scoped to multiple targets. "
                    "Pick one of project, domain or trust.")

        self.client_manager = clientmanager.ClientManager(
            auth_options=self.options,
            verify=self.verify,
            api_version=self.api_version,
        )
        return

    def initialize_app(self, argv):
        """Global app init bits:

        * set up API versions
        * validate authentication info
        * authenticate against Identity if requested
        """

        super(OpenStackShell, self).initialize_app(argv)

        # Save default domain
        self.default_domain = self.options.os_default_domain

        # Loop through extensions to get API versions
        for mod in clientmanager.PLUGIN_MODULES:
            version_opt = getattr(self.options, mod.API_VERSION_OPTION, None)
            if version_opt:
                api = mod.API_NAME
                self.api_version[api] = version_opt
                version = '.v' + version_opt.replace('.', '_')
                cmd_group = 'openstack.' + api.replace('-', '_') + version
                self.command_manager.add_command_group(cmd_group)
                self.log.debug(
                    '%(name)s API version %(version)s, cmd group %(group)s',
                    {'name': api, 'version': version_opt, 'group': cmd_group}
                )

        # Commands that span multiple APIs
        self.command_manager.add_command_group(
            'openstack.common')

        # This is the naive extension implementation referred to in
        # blueprint 'client-extensions'
        # Extension modules can register their commands in an
        # 'openstack.extension' entry point group:
        # entry_points={
        #     'openstack.extension': [
        #         'list_repo=qaz.github.repo:ListRepo',
        #         'show_repo=qaz.github.repo:ShowRepo',
        #     ],
        # }
        self.command_manager.add_command_group(
            'openstack.extension')
        # call InitializeXxx() here
        # set up additional clients to stuff in to client_manager??

        # Handle deferred help and exit
        if self.options.deferred_help:
            self.DeferredHelpAction(self.parser, self.parser, None, None)

        # Set up common client session
        if self.options.os_cacert:
            self.verify = self.options.os_cacert
        else:
            self.verify = not self.options.insecure

    def prepare_to_run_command(self, cmd):
        """Set up auth and API versions"""
        self.log.debug('prepare_to_run_command %s', cmd.__class__.__name__)

        if not cmd.auth_required:
            return
        if cmd.best_effort:
            try:
                self.authenticate_user()
            except Exception:
                pass
        else:
            self.authenticate_user()
        return

    def clean_up(self, cmd, result, err):
        self.log.debug('clean_up %s', cmd.__class__.__name__)

        if err:
            self.log.debug('got an error: %s', err)

        # Process collected timing data
        if self.options.timing:
            # Loop through extensions
            for mod in self.ext_modules:
                client = getattr(self.client_manager, mod.API_NAME)
                if hasattr(client, 'get_timings'):
                    self.timing_data.extend(client.get_timings())

            # Use the Timing pseudo-command to generate the output
            tcmd = timing.Timing(self, self.options)
            tparser = tcmd.get_parser('Timing')

            # If anything other than prettytable is specified, force csv
            format = 'table'
            # Check the formatter used in the actual command
            if hasattr(cmd, 'formatter') \
                    and cmd.formatter != cmd._formatter_plugins['table'].obj:
                format = 'csv'

            sys.stdout.write('\n')
            targs = tparser.parse_args(['-f', format])
            tcmd.run(targs)

    def interact(self):
        # NOTE(dtroyer): Maintain the old behaviour for interactive use as
        #                this path does not call prepare_to_run_command()
        self.authenticate_user()
        super(OpenStackShell, self).interact()


def main(argv=sys.argv[1:]):
    return OpenStackShell().run(argv)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
