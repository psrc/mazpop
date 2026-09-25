import argparse


class CLI:
    def __init__(self, version, description):
        self.version = version
        self.description = description
        self.parser = argparse.ArgumentParser(description=self.description)

        self.parser.add_argument(
            "--version", "-V", action="version", version=self.version
        )

        # print help if no subcommand is provided
        self.parser.set_defaults(func=lambda x: self.parser.print_help())

        # created lazily by add_subcommand so help stays clean when there
        # are no subcommands yet
        self.subparsers = None

    def add_subcommand(self, name, args_func, exec_func, description):
        if self.subparsers is None:
            self.subparsers = self.parser.add_subparsers(
                title="subcommands", help="available subcommand options"
            )
        subparser = self.subparsers.add_parser(name, description=description)
        args_func(subparser)
        subparser.set_defaults(func=exec_func)

    def execute(self):
        args = self.parser.parse_args()
        args.func(args)