"""Mark wheels that contain the optional Windows x64 helper appropriately."""
from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel


class DesktopWheel(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.has_wgc = Path(__file__).with_name("src").joinpath("wdc","wgc_capture.exe").is_file()
        if self.has_wgc:
            self.root_is_pure = False

    def get_tag(self):
        if self.has_wgc:
            return "py3","none","win_amd64"
        return super().get_tag()


setup(cmdclass={"bdist_wheel":DesktopWheel})
