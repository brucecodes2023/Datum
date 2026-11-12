# SPDX-License-Identifier: LGPL-2.1-or-later
"""Starter scripts for New Code Part (roadmap AUT-05).

Each template is a complete build123d script for bruceworx_codecad: its dimensions are declared
with param(), so a new part from a template is parametric from the first run. The dimension names
are generic on purpose; names already in the Parameters sheet keep the sheet's value.
TestBruceWorxCodeCad runs every template.
"""

from __future__ import annotations

TEMPLATES = (
    ("blank", "Blank part", "A box, to start from.", '''\
# A code part: build123d makes the shape, param() declares the dimensions.
from build123d import *

length = param("length", 40, "Length")
width = param("width", 20, "Width")
height = param("height", 10, "Height")

result = Box(length, width, height)
'''),
    ("plate_holes", "Plate with hole grid", "A rounded plate with a grid of through holes.", '''\
# Plate with a grid of through holes.
from build123d import *

plate_l = param("plate_l", 80, "Plate length")
plate_w = param("plate_w", 50, "Plate width")
plate_t = param("plate_t", 4, "Plate thickness")
corner_r = param("corner_r", 5, "Corner radius")
hole_d = param("hole_d", 3.4, "Hole diameter")
hole_inset = param("hole_inset", 8, "Hole centre to plate edge")
holes_x = param.count("holes_x", 3, "Holes along the length")
holes_y = param.count("holes_y", 2, "Holes along the width")

plan = RectangleRounded(plate_l, plate_w, corner_r)
grid = GridLocations(
    (plate_l - 2 * hole_inset) / max(holes_x - 1, 1), (plate_w - 2 * hole_inset) / max(holes_y - 1, 1),
    holes_x, holes_y)
plan -= [location * Circle(hole_d / 2) for location in grid]

result = extrude(plan, plate_t)
'''),
    ("bracket", "L bracket", "An angle bracket with an inside fillet and two holes per leg.", '''\
# L bracket: two legs, an inside fillet, two holes in each leg.
from build123d import *

leg_a = param("leg_a", 40, "Leg along X")
leg_b = param("leg_b", 30, "Leg along Z")
bracket_w = param("bracket_w", 30, "Width")
bracket_t = param("bracket_t", 4, "Thickness")
inside_r = param("inside_r", 4, "Inside fillet radius")
hole_d = param("hole_d", 4.3, "Hole diameter")

profile = Polyline((0, 0), (leg_a, 0), (leg_a, bracket_t), (bracket_t, bracket_t), (bracket_t, leg_b), (0, leg_b),
                   close=True)
face = make_face(profile)
if inside_r > 0:
    corner = face.vertices().sort_by_distance((bracket_t, bracket_t))[0]
    face = fillet(corner, inside_r)
bracket = extrude(Plane.XZ * face, -bracket_w)           # profile in XZ, width along +Y

spacing = bracket_w / 2
for y in (bracket_w / 2 - spacing / 2, bracket_w / 2 + spacing / 2):
    bracket -= Pos((leg_a + bracket_t + inside_r) / 2, y, bracket_t / 2) * Cylinder(hole_d / 2, 3 * bracket_t)
    bracket -= Pos(bracket_t / 2, y, (leg_b + bracket_t + inside_r) / 2) * Rot(0, 90, 0) * Cylinder(hole_d / 2, 3 * bracket_t)

result = bracket
'''),
    ("enclosure", "Enclosure and lid", "An open box with screw posts and a matching lid: two parts.", '''\
# Enclosure: a shelled box with a screw post in each corner, and a lid. Two parts from one script.
from build123d import *

box_l = param("box_l", 90, "Outside length")
box_w = param("box_w", 60, "Outside width")
box_h = param("box_h", 30, "Outside height without the lid")
wall_t = param("wall_t", 2, "Wall and floor thickness")
box_r = param("box_r", 6, "Outside corner radius")
post_d = param("post_d", 7, "Screw post diameter")
screw_d = param("screw_d", 2.6, "Screw pilot hole diameter")
lid_t = param("lid_t", 2.5, "Lid thickness")
lid_screw_d = param("lid_screw_d", 3.4, "Lid clearance hole diameter")

outer = extrude(RectangleRounded(box_l, box_w, box_r), box_h)
cavity = Pos(0, 0, wall_t) * extrude(
    RectangleRounded(box_l - 2 * wall_t, box_w - 2 * wall_t, max(box_r - wall_t, 0.5)), box_h)
body = outer - cavity

post_x, post_y = box_l / 2 - wall_t - post_d / 2, box_w / 2 - wall_t - post_d / 2
posts = [(sx * post_x, sy * post_y) for sx in (-1, 1) for sy in (-1, 1)]
for x, y in posts:
    body += Pos(x, y, wall_t) * Cylinder(post_d / 2, box_h - wall_t, align=(Align.CENTER, Align.CENTER, Align.MIN))
for x, y in posts:
    body -= Pos(x, y, wall_t + 2) * Cylinder(screw_d / 2, box_h, align=(Align.CENTER, Align.CENTER, Align.MIN))

lid_plan = RectangleRounded(box_l, box_w, box_r)
lid_plan -= [Pos(x, y) * Circle(lid_screw_d / 2) for x, y in posts]
lid = Pos(0, 0, box_h) * extrude(lid_plan, lid_t)

show_object(body, name="Enclosure")
show_object(lid, name="Lid")
'''),
    ("quad_frame", "Quadcopter frame plate", "A wide-X frame plate: body, four arms, motor and stack holes.", '''\
# Quadcopter frame plate: a body under the stack and four tapered arms to the motor pads.
# X forward, Y left; the top face (z = 0) is where the motors and the stack sit.
from math import atan2, degrees, hypot

from build123d import *

motor_x = param("motor_x", 47, "Motor position, forward of centre")
motor_y = param("motor_y", 70, "Motor position, sideways of centre")
frame_t = param("frame_t", 4, "Plate thickness")
body_l = param("body_l", 92, "Body length")
body_w = param("body_w", 46, "Body width")
body_r = param("body_r", 6, "Body corner radius")
arm_root_w = param("arm_root_w", 18, "Arm width at the body")
motor_pad_d = param("motor_pad_d", 21, "Motor pad diameter")
motor_pattern = param("motor_pattern", 9, "Motor screw square pattern")
motor_screw_d = param("motor_screw_d", 2.2, "Motor screw hole diameter")
motor_relief_d = param("motor_relief_d", 5, "Hole under the motor shaft")
stack_pattern = param("stack_pattern", 20, "Stack screw square pattern")
stack_screw_d = param("stack_screw_d", 2.2, "Stack screw hole diameter")


def disc(x, y, d):
    return Pos(x, y) * Circle(d / 2)


plan = RectangleRounded(body_l, body_w, body_r)
motors = [(sx * motor_x, sy * motor_y) for sx in (-1, 1) for sy in (-1, 1)]
for mx, my in motors:
    root_x = (1 if mx > 0 else -1) * min(body_l / 2 - arm_root_w / 2, abs(mx))
    root_y = (1 if my > 0 else -1) * (body_w / 2 - arm_root_w / 2)
    plan += make_hull(disc(root_x, root_y, arm_root_w).edges() + disc(mx, my, motor_pad_d).edges())

half = motor_pattern / 2
for mx, my in motors:
    yaw = degrees(atan2(-my, -mx))                     # one screw-pattern side faces the stack
    plan -= disc(mx, my, motor_relief_d)
    plan -= [Pos(mx, my) * Rot(0, 0, yaw) * disc(sx * half, sy * half, motor_screw_d)
             for sx in (-1, 1) for sy in (-1, 1)]
half = stack_pattern / 2
plan -= [disc(sx * half, sy * half, stack_screw_d) for sx in (-1, 1) for sy in (-1, 1)]

result = Pos(0, 0, -frame_t) * extrude(plan, frame_t)
'''),
)


def templates():
    """[(key, title, description, script)]."""
    return list(TEMPLATES)


def script(key):
    for item in TEMPLATES:
        if item[0] == key:
            return item[3]
    raise KeyError(key)
