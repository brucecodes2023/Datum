# SPDX-License-Identifier: LGPL-2.1-or-later

from Base.Metadata import export, constmethod
from Base.BaseClass import BaseClass
from typing import Any

@export(
    Include="Gui/Navigation/NavigationStyle.h",
)
class NavigationStyle(BaseClass):
    """
    This is the base class for navigation styles

    Author: Werner Mayer (wmayer@users.sourceforge.net)
    Licence: LGPL
    """

    # [BW] Rotation center access for the BruceWorx navigation pill.
    @constmethod
    def getRotationCenter(self) -> Any:
        """
        getRotationCenter() -> Base.Vector or None

        The point mouse orbits turn about, or None when there is none yet.
        """
        ...

    def setRotationCenter(self, point: Any) -> None:
        """
        setRotationCenter(point) -> None

        Set the point orbits turn about.
        """
        ...

    def findRotationCenter(self, x: int, y: int) -> Any:
        """
        findRotationCenter(x, y) -> Base.Vector or None

        Set the rotation center as a mouse orbit starting at viewport pixel
        (x, y) would (origin bottom left, device pixels) and return it. With
        the rotation mode "Window center" it picks at the cursor instead.
        """
        ...
