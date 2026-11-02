// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2024 Ondsel <development@ondsel.com>                     *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/

#include <cmath>
#include <vector>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObjectGroup.h>
#include <App/FeaturePythonPyImp.h>
#include <App/Link.h>
#include <App/PropertyPythonObject.h>
#include <Base/Console.h>
#include <Base/Placement.h>
#include <Base/Rotation.h>
#include <Base/Tools.h>
#include <Base/Interpreter.h>
#include <CXX/Objects.hxx>

#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/App/TopoShape.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/Part/App/DatumFeature.h>

#include "AssemblyObject.h"
#include "AssemblyUtils.h"
#include "JointGroup.h"

#include "AssemblyLink.h"
#include "AssemblyLinkPy.h"

namespace PartApp = Part;

using namespace Assembly;

// ================================ Assembly Object ============================

PROPERTY_SOURCE(Assembly::AssemblyLink, App::Part)

AssemblyLink::AssemblyLink()
{
    ADD_PROPERTY_TYPE(
        Rigid,
        (true),
        "General",
        (App::PropertyType)(App::Prop_None),
        "If the sub-assembly is set to Rigid, it will act "
        "as a rigid body. Else its joints will be taken into account."
    );

    ADD_PROPERTY_TYPE(
        LinkedObject,
        (nullptr),
        "General",
        (App::PropertyType)(App::Prop_None),
        "The linked assembly."
    );
}

AssemblyLink::~AssemblyLink() = default;

PyObject* AssemblyLink::getPyObject()
{
    if (PythonObject.is(Py::_None())) {
        // ref counter is set to 1
        PythonObject = Py::Object(new AssemblyLinkPy(this), true);
    }
    return Py::new_reference_to(PythonObject);
}

App::DocumentObjectExecReturn* AssemblyLink::execute()
{
    updateContents();

    return App::Part::execute();
}

void AssemblyLink::onChanged(const App::Property* prop)
{
    if (App::GetApplication().isRestoring()) {
        App::Part::onChanged(prop);
        return;
    }

    if (prop == &Rigid) {
        Base::Placement movePlc;

        // A flexible sub-assembly cannot be grounded.
        // If a rigid sub-assembly has an object that is grounded, we also remove it.
        auto groundedJoints = getParentAssembly()->getGroundedJoints();
        for (auto* joint : groundedJoints) {
            auto* propObj = dynamic_cast<App::PropertyLink*>(
                joint->getPropertyByName("ObjectToGround")
            );
            if (!propObj) {
                continue;
            }
            auto* groundedObj = propObj->getValue();
            if (auto* linkElt = dynamic_cast<App::LinkElement*>(groundedObj)) {
                // hasObject does not handle link groups so we must handle it manually.
                groundedObj = linkElt->getLinkGroup();
            }

            if (Rigid.getValue() ? hasObject(groundedObj) : groundedObj == this) {
                getDocument()->removeObject(joint->getNameInDocument());
            }
        }

        if (Rigid.getValue()) {
            // movePlc needs to be computed before updateContents.
            App::DocumentObject* firstLink = nullptr;
            for (auto* obj : Group.getValues()) {
                if (obj && (obj->isDerivedFrom<App::Link>() || obj->isDerivedFrom<AssemblyLink>())) {
                    firstLink = obj;
                    break;
                }
            }

            if (firstLink) {
                App::DocumentObject* sourceObj = nullptr;
                if (auto* link = dynamic_cast<App::Link*>(firstLink)) {
                    sourceObj = link->getLinkedObject(false);  // Get non-recursive linked object
                }
                else if (auto* asmLink = dynamic_cast<AssemblyLink*>(firstLink)) {
                    sourceObj = asmLink->getLinkedAssembly();
                }

                if (sourceObj) {
                    auto* propSource = dynamic_cast<App::PropertyPlacement*>(
                        sourceObj->getPropertyByName("Placement")
                    );
                    auto* propLink = dynamic_cast<App::PropertyPlacement*>(
                        firstLink->getPropertyByName("Placement")
                    );

                    if (propSource && propLink) {
                        movePlc = propLink->getValue() * propSource->getValue().inverse();
                    }
                }
            }
        }

        updateContents();

        auto* propPlc = dynamic_cast<App::PropertyPlacement*>(getPropertyByName("Placement"));
        if (!propPlc) {
            return;
        }

        if (!Rigid.getValue()) {
            // when the assemblyLink becomes flexible, we need to make sure its placement is
            // identity or it's going to mess up moving parts placement within.
            Base::Placement plc = propPlc->getValue();
            if (!plc.isIdentity()) {
                propPlc->setValue(Base::Placement());

                // We need to apply the placement of the assembly link to the children or they will
                // move.
                std::vector<App::DocumentObject*> group = Group.getValues();
                for (auto* obj : group) {
                    if (!obj->isDerivedFrom<App::Part>() && !obj->isDerivedFrom<PartApp::Feature>()
                        && !obj->isDerivedFrom<App::Link>()) {
                        continue;
                    }

                    if (obj->isLinkGroup()) {
                        auto* srcLink = static_cast<App::Link*>(obj);
                        const std::vector<App::DocumentObject*> srcElements
                            = srcLink->ElementList.getValues();

                        for (auto elt : srcElements) {
                            if (!elt) {
                                continue;
                            }

                            auto* prop = dynamic_cast<App::PropertyPlacement*>(
                                elt->getPropertyByName("Placement")
                            );
                            if (prop) {
                                prop->setValue(plc * prop->getValue());
                            }
                        }
                    }
                    else {
                        auto* prop = dynamic_cast<App::PropertyPlacement*>(
                            obj->getPropertyByName("Placement")
                        );
                        if (prop) {
                            prop->setValue(plc * prop->getValue());
                        }
                    }
                }

                AssemblyObject::redrawJointPlacements(getJoints());
            }
        }
        else {
            // For the assemblylink not to move to origin, we need to update its placement.
            if (!movePlc.isIdentity()) {
                propPlc->setValue(movePlc);
            }
        }
        return;
    }
    App::Part::onChanged(prop);
}

void AssemblyLink::updateParentJoints()
{
    AssemblyObject* parent = getParentAssembly();
    if (!parent) {
        return;
    }

    bool rigid = Rigid.getValue();
    // Iterate joints in the immediate parent assembly only (recursive=false)
    for (auto* joint : parent->getJoints(false, false, false)) {
        for (const char* refName : {"Reference1", "Reference2"}) {
            auto* prop = dynamic_cast<App::PropertyXLinkSub*>(joint->getPropertyByName(refName));
            if (!prop) {
                continue;
            }
            App::DocumentObject* refObj = prop->getValue();
            if (!refObj) {
                continue;
            }

            if (rigid) {  // Flexible -> Rigid
                if (hasObject(refObj)) {
                    // The joint currently points to a child (refObj) inside this AssemblyLink.
                    // We must repoint it to 'this' and prepend the child's name to the sub-elements.
                    std::vector<std::string> subs = prop->getSubValues();
                    std::vector<std::string> newSubs;
                    std::string prefix = refObj->getNameInDocument();
                    prefix += ".";
                    for (const auto& s : subs) {
                        newSubs.push_back(prefix + s);
                    }
                    prop->setValue(this);
                    prop->setSubValues(std::move(newSubs));
                }
            }
            else {  // Rigid -> Flexible
                if (refObj == this) {
                    // The joint currently points to 'this'.
                    // We must extract the child's name from the sub-element, point to the child,
                    // and strip the prefix.
                    std::vector<std::string> subs = prop->getSubValues();
                    if (subs.empty()) {
                        continue;
                    }
                    std::vector<std::string> parts = Base::Tools::splitSubName(subs[0]);
                    if (parts.empty()) {
                        continue;
                    }
                    std::string childName = parts[0];
                    App::DocumentObject* child = getDocument()->getObject(childName.c_str());
                    if (child && hasObject(child)) {
                        std::vector<std::string> newSubs;
                        size_t prefixLen = childName.length() + 1;  // "Name."
                        for (const auto& s : subs) {
                            if (s.length() >= prefixLen) {
                                newSubs.push_back(s.substr(prefixLen));
                            }
                            else {
                                newSubs.push_back(s);
                            }
                        }
                        prop->setValue(child);
                        prop->setSubValues(std::move(newSubs));
                    }
                }
            }
        }
        if (joint->isTouched()) {
            joint->recomputeFeature();
        }
    }
}

void AssemblyLink::updateContents()
{
    synchronizeComponents();

    if (isRigid()) {
        ensureNoJointGroup();
    }
    else {
        synchronizeJoints();
    }
    purgeTouched();
}

// [BW] Routed items (route paths, wires, hoses, pipes, bundles: Part::FeaturePython with a BendOK
// property) are not linked like parts; bruceworx_flexsub copies them into a flexible AssemblyLink.
static bool isRoutedItem(App::DocumentObject* obj)
{
    return obj && obj->getTypeId().getName() == std::string("Part::FeaturePython")
        && obj->getPropertyByName("BendOK") != nullptr;
}

static bool isRoutedCopy(App::DocumentObject* obj)
{
    return obj && obj->getPropertyByName("FlexSource") != nullptr;
}

// [BW] Hand the component map to bruceworx_flexsub.sync(link, {name: link}, assembly), which makes,
// updates and removes the routed copies. Silent when the BruceWorx module is not installed.
void AssemblyLink::synchronizeRoutedItems(AssemblyObject* assembly)
{
    Base::PyGILStateLocker lock;
    PyObject* module = PyImport_ImportModule("bruceworx_flexsub");
    if (!module) {
        PyErr_Clear();
        return;
    }
    try {
        Py::Module mod(module, true);
        Py::Dict map;
        for (const auto& [src, lnk] : objLinkMap) {
            if (src && lnk && src->getNameInDocument()) {
                map.setItem(src->getNameInDocument(), Py::asObject(lnk->getPyObject()));
            }
        }
        Py::Callable sync(mod.getAttr("sync"));
        Py::Tuple args(3);
        args.setItem(0, Py::asObject(getPyObject()));
        args.setItem(1, map);
        args.setItem(2, Py::asObject(assembly->getPyObject()));
        sync.apply(args);
    }
    catch (Py::Exception&) {
        Base::PyException e;
        e.reportException();
    }
}

// [BW] Nested flexible sub-assemblies (Top > Gantry > X Axis, all flexible).
// A flexible AssemblyLink keeps an identity placement and its child links carry the placements
// the solver works with. A flexible AssemblyLink inside another one (a "nested" one) has its
// children seated here like the outer one seats its own: from the layout of what it links.
static bool isNestedInAssemblyLink(const AssemblyLink* link)
{
    for (auto* obj : link->getInList()) {
        if (obj->isDerivedFrom<AssemblyLink>()) {
            return true;
        }
    }
    return false;
}

// The placement of the component 'obj' of the linked assembly, in the layout of the linked object:
// the component's own placement when the link shows an assembly, the placement of the child link
// that shows it when the link shows another (flexible, already laid out) AssemblyLink.
static bool sourcePlacement(const AssemblyLink* link, App::DocumentObject* obj, Base::Placement& plc)
{
    auto* src = freecad_cast<AssemblyLink*>(link->LinkedObject.getValue());
    if (!src) {
        auto* prop = dynamic_cast<App::PropertyPlacement*>(obj->getPropertyByName("Placement"));
        if (!prop) {
            return false;
        }
        plc = prop->getValue();
        return true;
    }
    for (auto* child : src->Group.getValues()) {
        App::DocumentObject* shown = nullptr;
        if (auto* childAsm = freecad_cast<AssemblyLink*>(child)) {
            shown = childAsm->getLinkedObject2(false);
        }
        else if (auto* childLink = dynamic_cast<App::Link*>(child)) {
            shown = childLink->getLinkedObject(false);
        }
        if (shown != obj) {
            continue;
        }
        auto* prop = dynamic_cast<App::PropertyPlacement*>(child->getPropertyByName("Placement"));
        if (!prop) {
            return false;
        }
        plc = prop->getValue();
        return true;
    }
    return false;
}

void AssemblyLink::synchronizeComponents()
{
    App::Document* doc = getDocument();

    AssemblyObject* assembly = getLinkedAssembly();
    if (!assembly) {
        return;
    }

    objLinkMap.clear();

    // [BW] A nested flexible AssemblyLink is given its outer link's frame as a placement (see
    // onChanged). It moves that into its children, so that it stays at the identity.
    const bool seatChildren = !isRigid() && isNestedInAssemblyLink(this);
    if (seatChildren && !Placement.getValue().isIdentity()) {
        const Base::Placement frame = Placement.getValue();
        for (auto* child : Group.getValues()) {
            if (child && (child->isDerivedFrom<App::Link>() || child->isDerivedFrom<AssemblyLink>())
                && !child->isLinkGroup() && !isRoutedCopy(child)) {
                auto* prop
                    = dynamic_cast<App::PropertyPlacement*>(child->getPropertyByName("Placement"));
                if (prop) {
                    prop->setValue(frame * prop->getValue());
                }
            }
        }
        Placement.setValue(Base::Placement());
    }

    std::vector<App::DocumentObject*> assemblyGroup = assembly->Group.getValues();
    std::vector<App::DocumentObject*> assemblyLinkGroup = Group.getValues();

    // Filter out child objects from Part-workbench features to get only top-level components.
    // An object is considered a child if it's referenced by another object's 'Base', 'Tool',
    // or 'Shapes' property within the same group.
    std::set<App::DocumentObject*> children;
    for (auto* obj : assemblyGroup) {
        if (auto* partFeat = dynamic_cast<PartApp::Feature*>(obj)) {
            if (auto* prop = dynamic_cast<App::PropertyLink*>(partFeat->getPropertyByName("Base"))) {
                if (prop->getValue()) {
                    children.insert(prop->getValue());
                }
            }
            if (auto* prop = dynamic_cast<App::PropertyLink*>(partFeat->getPropertyByName("Tool"))) {
                if (prop->getValue()) {
                    children.insert(prop->getValue());
                }
            }
            if (auto* prop
                = dynamic_cast<App::PropertyLinkList*>(partFeat->getPropertyByName("Shapes"))) {
                for (auto* shapeObj : prop->getValues()) {
                    children.insert(shapeObj);
                }
            }
        }
    }

    std::vector<App::DocumentObject*> topLevelComponents;
    std::copy_if(
        assemblyGroup.begin(),
        assemblyGroup.end(),
        std::back_inserter(topLevelComponents),
        [&children](App::DocumentObject* obj) {
            // [BW] routed items are copied by synchronizeRoutedItems, not linked
            return children.find(obj) == children.end() && !isRoutedItem(obj);
        }
    );

    // We check if a component needs to be added to the AssemblyLink
    for (auto* obj : topLevelComponents) {
        if (!obj->isDerivedFrom<App::Part>() && !obj->isDerivedFrom<PartApp::Feature>()
            && !obj->isDerivedFrom<App::Link>()) {
            continue;
        }

        // Note, the user can have nested sub-assemblies.
        // In which case we need to add an AssemblyLink and not a Link.
        App::DocumentObject* link = nullptr;
        bool found = false;
        std::set<App::Link*> linkGroupsAdded;

        for (auto* obj2 : assemblyLinkGroup) {
            App::DocumentObject* linkedObj;

            auto* subAsmLink = freecad_cast<AssemblyLink*>(obj2);
            auto* link2 = dynamic_cast<App::Link*>(obj2);

            if (subAsmLink) {
                linkedObj = subAsmLink->getLinkedObject2(false);  // not recursive
            }
            else if (link2) {
                if (obj->isLinkGroup() && link2->isLinkGroup()) {
                    auto* srcLink = static_cast<App::Link*>(obj);
                    if ((srcLink->getTrueLinkedObject(false) == link2->getTrueLinkedObject(false))
                        && link2->ElementCount.getValue() == srcLink->ElementCount.getValue()
                        && linkGroupsAdded.find(srcLink) == linkGroupsAdded.end()) {
                        found = true;
                        link = obj2;
                        // In case where there are more than 2 link groups with the
                        // same number of elements.
                        linkGroupsAdded.insert(srcLink);

                        const std::vector<App::DocumentObject*> srcElements
                            = srcLink->ElementList.getValues();
                        const std::vector<App::DocumentObject*> newElements
                            = link2->ElementList.getValues();
                        for (size_t i = 0; i < srcElements.size(); ++i) {
                            objLinkMap[srcElements[i]] = newElements[i];
                        }
                        break;
                    }
                }
                else if (obj->isLinkGroup() && !link2->isLinkGroup()) {
                    continue;  // make sure we migrate sub assemblies that had link to linkgroups
                }
                linkedObj = link2->getLinkedObject(false);  // not recursive
            }
            else {
                // We consider only Links and AssemblyLinks
                continue;
            }

            if (linkedObj == obj) {
                found = true;
                link = obj2;
                break;
            }
        }
        if (!found) {
            // Add a link or a AssemblyLink to it in the AssemblyLink.
            if (obj->isDerivedFrom<AssemblyLink>()) {
                auto* asmLink = static_cast<AssemblyLink*>(obj);

                App::DocumentObject* newObj
                    = doc->addObject("Assembly::AssemblyLink", obj->getNameInDocument());
                auto* subAsmLink = static_cast<AssemblyLink*>(newObj);
                subAsmLink->LinkedObject.setValue(obj);
                subAsmLink->Label.setValue(obj->Label.getValue());
                addObject(subAsmLink);
                // [BW] after addObject: setting Rigid fills the new link, and a nested flexible one
                // must know that it is nested to seat its children.
                subAsmLink->Rigid.setValue(asmLink->Rigid.getValue());
                link = subAsmLink;
                if (seatChildren) {
                    Base::Placement plc;  // [BW] a flexible one has the identity, but make sure
                    if (sourcePlacement(this, obj, plc)) {
                        subAsmLink->Placement.setValue(plc);
                    }
                }
            }
            else if (obj->isDerivedFrom<App::Link>() && obj->isLinkGroup()) {
                auto* srcLink = static_cast<App::Link*>(obj);

                auto* newLink = static_cast<App::Link*>(
                    doc->addObject("App::Link", obj->getNameInDocument())
                );
                newLink->LinkedObject.setValue(srcLink->getTrueLinkedObject(false));

                newLink->Label.setValue(obj->Label.getValue());
                addObject(newLink);

                newLink->ElementCount.setValue(srcLink->ElementCount.getValue());
                const std::vector<App::DocumentObject*> srcElements = srcLink->ElementList.getValues();
                const std::vector<App::DocumentObject*> newElements = newLink->ElementList.getValues();
                for (size_t i = 0; i < srcElements.size(); ++i) {
                    auto* newObj = newElements[i];
                    auto* srcObj = srcElements[i];
                    if (newObj && srcObj) {
                        syncPlacements(srcObj, newObj);
                    }
                    objLinkMap[srcObj] = newObj;
                }

                link = newLink;
            }
            else {
                App::DocumentObject* newObj = doc->addObject("App::Link", obj->getNameInDocument());
                auto* newLink = static_cast<App::Link*>(newObj);
                newLink->LinkedObject.setValue(obj);
                newLink->Label.setValue(obj->Label.getValue());
                addObject(newLink);
                link = newLink;
                if (seatChildren) {
                    // [BW] Seat it like its source: a new link would sit at the identity until
                    // the solver moves it, and the solver does not reach it from outside.
                    Base::Placement plc;
                    if (sourcePlacement(this, obj, plc)) {
                        newLink->Placement.setValue(plc);
                    }
                }
            }
        }

        objLinkMap[obj] = link;
    }

    // If the assemblyLink is rigid, then we keep all placements synchronized.
    if (isRigid()) {
        for (const auto& [sourceObj, linkObj] : objLinkMap) {
            syncPlacements(sourceObj, linkObj);
        }
    }

    // We check if a component needs to be removed from the AssemblyLink
    // NOTE: this is not being executed when a src link is deleted, because the link
    // is then in error, and so AssemblyLink::execute() does not get called.
    std::set<App::DocumentObject*> validLinks;
    for (const auto& pair : objLinkMap) {
        validLinks.insert(pair.second);
    }
    for (auto* obj : assemblyLinkGroup) {
        // We don't need to update assemblyLinkGroup after the addition since we're not removing
        // something we just added.
        if (!obj->isDerivedFrom<App::Part>() && !obj->isDerivedFrom<PartApp::Feature>()
            && !obj->isDerivedFrom<App::Link>()) {
            continue;
        }
        if (isRoutedCopy(obj)) {
            continue;  // [BW] owned by synchronizeRoutedItems
        }
        if (validLinks.find(obj) == validLinks.end()) {
            doc->removeObject(obj->getNameInDocument());
        }
    }

    // [BW] Flexible: copy the sub-assembly's routed items with attachments re-pointed at the links.
    // Rigid: remove those copies.
    synchronizeRoutedItems(assembly);
}

namespace
{
template<typename T>
void copyPropertyIfDifferent(
    App::DocumentObject* source,
    App::DocumentObject* target,
    const char* propertyName
)
{
    auto sourceProp = freecad_cast<T*>(source->getPropertyByName(propertyName));
    auto targetProp = freecad_cast<T*>(target->getPropertyByName(propertyName));
    if (sourceProp && targetProp && sourceProp->getValue() != targetProp->getValue()) {
        targetProp->setValue(sourceProp->getValue());
    }
}

std::string removeUpToName(const std::string& sub, const std::string& name)
{
    size_t pos = sub.find(name);
    if (pos != std::string::npos) {
        // Move the position to the character after the found substring and the following '.'
        pos += name.length() + 1;
        if (pos < sub.length()) {
            return sub.substr(pos);
        }
    }
    // If s2 is not found in s1, return the original string
    return sub;
}

std::string replaceLastOccurrence(
    const std::string& str,
    const std::string& oldStr,
    const std::string& newStr
)
{
    size_t pos = str.rfind(oldStr);
    if (pos != std::string::npos) {
        std::string result = str;
        result.replace(pos, oldStr.length(), newStr);
        return result;
    }
    return str;
}
};  // namespace

void AssemblyLink::synchronizeJoints()
{
    App::Document* doc = getDocument();
    AssemblyObject* assembly = getLinkedAssembly();
    if (!assembly) {
        return;
    }

    JointGroup* jGroup = ensureJointGroup();

    // [BW] A nested flexible AssemblyLink made by synchronizeComponents has no children yet
    // (it executes after this one), and the joints copied below name them: fill it now.
    for (const auto& [srcObj, linkObj] : objLinkMap) {
        auto* lnkAsm = freecad_cast<AssemblyLink*>(linkObj);
        if (lnkAsm && !lnkAsm->isRigid() && lnkAsm->Group.getValues().empty()) {
            lnkAsm->updateContents();
        }
    }

    std::vector<App::DocumentObject*> assemblyJoints
        = assembly->getJoints(assembly->isTouched(), false, false);
    std::vector<App::DocumentObject*> assemblyLinkJoints = getJoints();

    // We delete the excess of joints if any
    for (size_t i = assemblyJoints.size(); i < assemblyLinkJoints.size(); ++i) {
        doc->removeObject(assemblyLinkJoints[i]->getNameInDocument());
    }

    // We make sure the joints match.
    for (size_t i = 0; i < assemblyJoints.size(); ++i) {
        App::DocumentObject* joint = assemblyJoints[i];
        App::DocumentObject* lJoint;
        if (i < assemblyLinkJoints.size()) {
            lJoint = assemblyLinkJoints[i];
        }
        else {
            auto ret = doc->copyObject({joint});
            if (ret.size() != 1) {
                continue;
            }
            lJoint = ret[0];
            jGroup->addObject(lJoint);
        }

        // Then we have to check the properties one by one.
        copyPropertyIfDifferent<App::PropertyBool>(joint, lJoint, "Suppressed");
        copyPropertyIfDifferent<App::PropertyFloat>(joint, lJoint, "Distance");
        copyPropertyIfDifferent<App::PropertyFloat>(joint, lJoint, "Distance2");
        copyPropertyIfDifferent<App::PropertyEnumeration>(joint, lJoint, "JointType");
        copyPropertyIfDifferent<App::PropertyPlacement>(joint, lJoint, "Offset1");
        copyPropertyIfDifferent<App::PropertyPlacement>(joint, lJoint, "Offset2");

        copyPropertyIfDifferent<App::PropertyBool>(joint, lJoint, "Detach1");
        copyPropertyIfDifferent<App::PropertyBool>(joint, lJoint, "Detach2");

        copyPropertyIfDifferent<App::PropertyFloat>(joint, lJoint, "AngleMax");
        copyPropertyIfDifferent<App::PropertyFloat>(joint, lJoint, "AngleMin");
        copyPropertyIfDifferent<App::PropertyFloat>(joint, lJoint, "LengthMax");
        copyPropertyIfDifferent<App::PropertyFloat>(joint, lJoint, "LengthMin");
        copyPropertyIfDifferent<App::PropertyBool>(joint, lJoint, "EnableAngleMax");
        copyPropertyIfDifferent<App::PropertyBool>(joint, lJoint, "EnableAngleMin");
        copyPropertyIfDifferent<App::PropertyBool>(joint, lJoint, "EnableLengthMax");
        copyPropertyIfDifferent<App::PropertyBool>(joint, lJoint, "EnableLengthMin");

        // The reference needs to be handled specifically
        handleJointReference(joint, lJoint, "Reference1");
        handleJointReference(joint, lJoint, "Reference2");
    }

    assemblyLinkJoints = getJoints();

    for (auto* joint : assemblyLinkJoints) {
        joint->purgeTouched();
    }
}


// [BW] A copied joint of a flexible sub-assembly can name a component inside a nested flexible
// AssemblyLink of that sub-assembly (Gantry's joint on a part of its X Axis). Find the local copy:
// the child of the matching nested link in 'lnk' that shows the same component.
static App::DocumentObject* shownBy(App::DocumentObject* child)
{
    if (auto* childAsm = freecad_cast<AssemblyLink*>(child)) {
        return childAsm->getLinkedObject2(false);
    }
    if (auto* childLink = dynamic_cast<App::Link*>(child)) {
        return childLink->getLinkedObject(false);
    }
    return nullptr;
}

static App::DocumentObject* mapIntoNested(
    AssemblyLink* src,
    AssemblyLink* lnk,
    App::DocumentObject* target
)
{
    for (auto* child : src->Group.getValues()) {
        if (!child) {
            continue;
        }
        App::DocumentObject* shown = shownBy(child);
        if (!shown) {
            continue;
        }
        App::DocumentObject* mine = nullptr;  // the child of lnk that shows the same
        for (auto* cand : lnk->Group.getValues()) {
            if (cand && shownBy(cand) == shown) {
                mine = cand;
                break;
            }
        }
        if (!mine) {
            continue;
        }
        if (child == target) {
            return mine;
        }
        auto* childAsm = freecad_cast<AssemblyLink*>(child);
        auto* mineAsm = freecad_cast<AssemblyLink*>(mine);
        if (childAsm && mineAsm) {
            if (auto* found = mapIntoNested(childAsm, mineAsm, target)) {
                return found;
            }
        }
    }
    return nullptr;
}

void AssemblyLink::handleJointReference(
    App::DocumentObject* joint,
    App::DocumentObject* lJoint,
    const char* refName
)
{
    auto prop1 = dynamic_cast<App::PropertyXLinkSub*>(joint->getPropertyByName(refName));
    auto prop2 = dynamic_cast<App::PropertyXLinkSub*>(lJoint->getPropertyByName(refName));
    if (!prop1 || !prop2) {
        return;
    }

    // 1. Get the external component prop1 is [ExternalPart, "Sub"]
    App::DocumentObject* externalComponent = prop1->getValue();
    if (!externalComponent) {
        return;
    }

    // 2. Map to local link
    auto it = objLinkMap.find(externalComponent);
    App::DocumentObject* nestedLink = nullptr;
    if (it == objLinkMap.end()) {
        // [BW] a component of a nested flexible AssemblyLink
        for (const auto& [srcObj, linkObj] : objLinkMap) {
            auto* srcAsm = freecad_cast<AssemblyLink*>(srcObj);
            auto* lnkAsm = freecad_cast<AssemblyLink*>(linkObj);
            if (srcAsm && lnkAsm && !srcAsm->isRigid()) {
                nestedLink = mapIntoNested(srcAsm, lnkAsm, externalComponent);
                if (nestedLink) {
                    break;
                }
            }
        }
    }
    if (it == objLinkMap.end() && !nestedLink) {
        Base::Console().warning(
            "AssemblyLink: Could not map external component %s to a local link for joint %s\n",
            externalComponent->getNameInDocument(),
            joint->getNameInDocument()
        );
        return;
    }
    App::DocumentObject* localLink = nestedLink ? nestedLink : it->second;

    // 3. Set the new reference
    // The local joint now points to the local link [LocalLink, "Sub"]
    if (prop2->getValue() != localLink) {
        prop2->setValue(localLink);
    }

    // 4. Sync sub-elements
    // The sub-elements (e.g. "Body.Face1") are relative to the component.
    // Since the LocalLink points to the ExternalPart, the relative path is identical.
    std::vector<std::string> subs1 = prop1->getSubValues();
    std::vector<std::string> subs2 = prop2->getSubValues();

    bool changed = false;
    if (subs1.size() != subs2.size()) {
        changed = true;
    }
    else {
        for (size_t i = 0; i < subs1.size(); ++i) {
            if (subs1[i] != subs2[i]) {
                changed = true;
                break;
            }
        }
    }

    if (changed) {
        prop2->setSubValues(std::move(subs1));
    }
}

void AssemblyLink::ensureNoJointGroup()
{
    // Make sure there is no joint group
    JointGroup* jGroup = getJointGroup(this);
    if (jGroup) {
        // If there is a joint group, we delete it and its content.
        jGroup->removeObjectsFromDocument();
        getDocument()->removeObject(jGroup->getNameInDocument());
    }
}
JointGroup* AssemblyLink::ensureJointGroup()
{
    // Make sure there is a jointGroup
    JointGroup* jGroup = getJointGroup(this);
    if (!jGroup) {
        jGroup = new JointGroup();
        getDocument()->addObject(jGroup, tr("Joints").toStdString().c_str());

        std::vector<DocumentObject*> grp = Group.getValues();
        grp.insert(grp.begin(), jGroup);
        Group.setValues(grp);
    }
    return jGroup;
}

App::DocumentObject* AssemblyLink::getLinkedObject2(bool recursive) const
{
    auto* obj = LinkedObject.getValue();
    auto* assembly = freecad_cast<AssemblyObject*>(obj);
    if (assembly) {
        return assembly;
    }
    else {
        auto* assemblyLink = freecad_cast<AssemblyLink*>(obj);
        if (assemblyLink) {
            if (recursive) {
                return assemblyLink->getLinkedObject2(recursive);
            }
            else {
                return assemblyLink;
            }
        }
    }

    return nullptr;
}

AssemblyObject* AssemblyLink::getLinkedAssembly() const
{
    return freecad_cast<AssemblyObject*>(getLinkedObject2());
}

AssemblyObject* AssemblyLink::getParentAssembly() const
{
    std::vector<App::DocumentObject*> inList = getInList();
    for (auto* obj : inList) {
        auto* assembly = freecad_cast<AssemblyObject*>(obj);
        if (assembly) {
            return assembly;
        }
    }

    return nullptr;
}

bool AssemblyLink::isRigid() const
{
    auto* prop = dynamic_cast<App::PropertyBool*>(getPropertyByName("Rigid"));
    if (!prop) {
        return true;
    }
    return prop->getValue();
}

std::vector<App::DocumentObject*> AssemblyLink::getJoints()
{
    JointGroup* jointGroup = getJointGroup(this);

    if (!jointGroup) {
        return {};
    }
    return jointGroup->getJoints();
}

bool AssemblyLink::allowDuplicateLabel() const
{
    return true;
}

int AssemblyLink::numberOfComponents() const
{
    return isRigid() ? 1 : getLinkedAssembly()->numberOfComponents();
}

bool AssemblyLink::isEmpty() const
{
    return numberOfComponents() == 0;
}
