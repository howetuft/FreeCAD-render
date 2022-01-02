# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2022 Howetuft <howetuft@gmail.com>                      *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

"""This module implements a renderer executor object for Render workbench.

The renderer executor allows to run a rendering engine in a responsive,
non-blocking way, provided a command line that should have been generated by a
renderer plugin, and to display the rendering result (image) in FreeCAD
graphical interface.
"""

import threading
import shlex
from subprocess import Popen, PIPE, STDOUT


from PySide.QtGui import (
    QLabel,
    QPixmap,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QPalette,
)

from PySide.QtCore import Qt

import FreeCAD as App
import FreeCADGui as Gui


class RendererExecutor(threading.Thread):
    """A class to execute a rendering engine.

    This class is designed to run a renderer in a separate thread, keeping
    console/gui responsive.  Meanwhile, stdout/stderr are piped to FreeCAD
    console, in such a way it is possible to follow the evolution of the
    rendering.
    To achieve that, renderer is executed in a separate thread.
    """

    def __init__(self, cmd, img, subw):
        """Initialize executor.

        Args:
            cmd -- command to execute (str)
            img -- path to resulting image (the renderer output) (str)
            subw -- the subwindow where to display the resulting image
        """
        super().__init__()
        self.cmd = str(cmd)
        self.img = str(img)
        self.subwindow = subw

    def run(self):
        """Run executor.

        This method represents the thread activity. It is not intended to be
        called directly (see 'threading' module documentation).
        """
        # TODO Test in Windows

        App.Console.PrintMessage(f"Starting rendering...\n{self.cmd}\n")
        try:
            with Popen(
                shlex.split(self.cmd),
                stdout=PIPE,
                stderr=STDOUT,
                bufsize=1,
                universal_newlines=True,
            ) as proc:
                for line in proc.stdout:
                    App.Console.PrintMessage(line)
        except Exception as err:
            errclass = err.__class__.__name__
            errmsg = str(err)
            App.Console.PrintError(f"{errclass}: {errmsg}\n")
            App.Console.PrintMessage("Aborting rendering...\n")
        else:
            rcode = proc.returncode
            msg = f"Exiting rendering - Return code: {rcode}\n"
            if not rcode:
                App.Console.PrintMessage(msg)
            else:
                App.Console.PrintWarning(msg)

            # Open result in GUI if relevant
            if self.img:
                if App.GuiUp:
                    try:
                        self.subwindow.widget().load_image(self.img)
                        self.subwindow.showMaximized()
                    except RuntimeError:
                        App.Console.PrintWarning(
                            "Warning: Could not load rendering result"
                        )
                else:
                    App.Console.PrintMessage(
                        f"Output file written to '{self.img}'\n"
                    )


def create_imageview_subwindow():
    """Create a subwindow in FreeCAD Gui to display an image."""
    if App.GuiUp:
        viewer = ImageView()
        mdiarea = Gui.getMainWindow().centralWidget()
        subw = mdiarea.addSubWindow(viewer)
        subw.setWindowTitle("Rendering result")
        subw.setVisible(False)
    else:
        subw = None
    return subw


class ImageView(QWidget):
    """A custom widget to display an image in FreeCAD Gui."""
    # Inspired by :
    # https://doc.qt.io/qt-6/qtwidgets-widgets-imageviewer-example.html
    # https://code.qt.io/cgit/pyside/pyside-setup.git/tree/examples/widgets/imageviewer
    def __init__(self, parent=None):
        """Initialize Widget."""
        super().__init__(parent)
        self.setLayout(QVBoxLayout())

        self.imglabel = QLabel()
        self.imglabel.setBackgroundRole(QPalette.Base)
        # self.imglabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.namelabel = QLabel()

        self.scrollarea = QScrollArea()
        self.scrollarea.setWidget(self.imglabel)
        self.scrollarea.setWidgetResizable(True)
        self.imglabel.setAlignment(Qt.AlignCenter)

        self.layout().addWidget(self.scrollarea)
        self.layout().addWidget(self.namelabel)
        self.imglabel.setText("(No image yet)")

    def load_image(self, img_path):
        """Load an image in widget from a file.

        Args:
            img_path -- Path of image file to load (str)
        """
        self.imglabel.setPixmap(QPixmap(img_path))
        self.namelabel.setText(img_path)
