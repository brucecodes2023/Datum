// SPDX-License-Identifier: LGPL-2.1-or-later
/***************************************************************************
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

// inclusion of the generated files (generated out of NavigationStylePy.pyi)
#include "Navigation/NavigationStylePy.h"
#include "Navigation/NavigationStylePy.cpp"

#include <Base/VectorPy.h>  // [BW]


using namespace Gui;

/** @class NavigationStylePy
 * The NavigationStyle Python class provides additional methods for manipulation of
 * navigation style objects.
 * @see NavigationStyle
 */

// returns a string which represent the object e.g. when printed in python
std::string NavigationStylePy::representation() const
{
    return {"<NavigationStyle object>"};
}

// [BW] Rotation center access for the BruceWorx navigation pill.
namespace
{
PyObject* rotationCenterOrNone(const NavigationStyle* style)
{
    SbBool found = false;
    SbVec3f center = style->getRotationCenter(found);
    if (!found) {
        Py_Return;
    }
    return new Base::VectorPy(Base::Vector3d(center[0], center[1], center[2]));
}
}  // namespace

PyObject* NavigationStylePy::getRotationCenter(PyObject* args) const
{
    if (!PyArg_ParseTuple(args, "")) {
        return nullptr;
    }
    return rotationCenterOrNone(getNavigationStylePtr());
}

PyObject* NavigationStylePy::setRotationCenter(PyObject* args)
{
    PyObject* point = nullptr;
    if (!PyArg_ParseTuple(args, "O!", &Base::VectorPy::Type, &point)) {
        return nullptr;
    }
    Base::Vector3d v = *static_cast<Base::VectorPy*>(point)->getVectorPtr();
    getNavigationStylePtr()->setRotationCenter(SbVec3f(float(v.x), float(v.y), float(v.z)));
    Py_Return;
}

PyObject* NavigationStylePy::findRotationCenter(PyObject* args)
{
    int x = 0;
    int y = 0;
    if (!PyArg_ParseTuple(args, "ii", &x, &y)) {
        return nullptr;
    }
    getNavigationStylePtr()->findRotationCenter(SbVec2s(short(x), short(y)));
    return rotationCenterOrNone(getNavigationStylePtr());
}

PyObject* NavigationStylePy::getCustomAttributes(const char* /*attr*/) const
{
    return nullptr;
}

int NavigationStylePy::setCustomAttributes(const char* /*attr*/, PyObject* /*obj*/)
{
    return 0;
}
