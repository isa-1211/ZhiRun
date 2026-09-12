// ZhiRun 250 x 210 mm mobile chassis for a 280 x 280 mm print bed.
// Set part to one of: layout, base_left, base_right, swing_arm, tower,
// electronics_tray, mast, wheel_hub, cable_clip.

$fn = 64;
part = "layout";
plate = 1; // Set to 1, 2, or 3 when part == "plate".

chassis_x = 250;
chassis_y = 210;
plate_t = 6;
half_x = 125;
wheel_d = 110;
wheel_axle_d = 8;
mount_hole_d = 4.4;

module rounded_box(s, r) {
    hull() {
        for (x = [r, s[0] - r])
            for (y = [r, s[1] - r])
                translate([x, y, 0]) cylinder(h = s[2], r = r);
    }
}

module bolt_hole(x, y, d = mount_hole_d, h = 20) {
    translate([x, y, -2]) cylinder(h = h, d = d);
}

module slot(x, y, length = 12, width = 4.4, h = 20) {
    hull() {
        translate([x - length / 2, y, -2]) cylinder(h = h, d = width);
        translate([x + length / 2, y, -2]) cylinder(h = h, d = width);
    }
}

module base_half(left = true) {
    difference() {
        union() {
            rounded_box([half_x, chassis_y, plate_t], 8);
            // Perimeter ribs protect the plate from twisting on uneven ground.
            translate([8, 8, plate_t]) cube([half_x - 16, 4, 8]);
            translate([8, chassis_y - 12, plate_t]) cube([half_x - 16, 4, 8]);
            translate([8, 8, plate_t]) cube([4, chassis_y - 16, 8]);
            translate([half_x - 12, 8, plate_t]) cube([4, chassis_y - 16, 8]);
            // A 10 mm overlapping center flange joins the two halves.
            if (left)
                translate([half_x - 10, 0, 0]) cube([10, chassis_y, plate_t + 2]);
            else
                translate([0, 0, 0]) cube([10, chassis_y, plate_t + 2]);
        }
        for (y = [28, 105, 182])
            bolt_hole(left ? half_x - 5 : 5, y, 5.2, 30);
        // Universal suspension tower slots.
        for (x = [20, 105])
            for (y = [25, chassis_y - 25])
                slot(x, y, 10, mount_hole_d, 30);
        // Electronics tray and battery tray mounting patterns.
        for (x = [48, 108])
            for (y = [68, 142])
                bolt_hole(x, y, mount_hole_d, 30);
    }
}

module swing_arm() {
    difference() {
        union() {
            hull() {
                translate([16, 18, 0]) cylinder(h = 12, d = 30);
                translate([104, 18, 0]) cylinder(h = 12, d = 30);
            }
            translate([47, 2, 0]) cube([42, 32, 12]);
            // Shock absorber eye and a replaceable TPU spring seat.
            translate([62, 18, 12]) cylinder(h = 6, d = 24);
        }
        translate([16, 18, -2]) cylinder(h = 20, d = wheel_axle_d + 0.4);
        translate([104, 18, -2]) cylinder(h = 20, d = wheel_axle_d + 0.4);
        translate([62, 18, -2]) cylinder(h = 24, d = 6.4);
        for (x = [39, 85])
            translate([x, 18, -2]) cylinder(h = 20, d = 5.2);
    }
}

module suspension_tower() {
    difference() {
        union() {
            rounded_box([42, 34, 48], 4);
            translate([4, 4, 44]) cube([34, 26, 8]);
        }
        // Pivot bolt passes through the tower along Y.
        translate([21, -2, 22]) rotate([90, 0, 0]) cylinder(h = 38, d = 8.4);
        // Two M4 base bolts.
        for (x = [10, 32])
            translate([x, 17, -2]) cylinder(h = 20, d = 4.4);
        // Upper shock eye.
        translate([21, -2, 43]) rotate([90, 0, 0]) cylinder(h = 38, d = 6.4);
    }
}

module electronics_tray() {
    difference() {
        union() {
            rounded_box([180, 130, 4], 6);
            // Cable-protection lip and four M4 standoffs.
            translate([4, 4, 4]) cube([172, 3, 18]);
            translate([4, 123, 4]) cube([172, 3, 18]);
            for (x = [18, 162])
                for (y = [18, 112])
                    translate([x, y, 4]) cylinder(h = 22, d = 12);
        }
        for (x = [18, 162])
            for (y = [18, 112])
                bolt_hole(x, y, 4.4, 34);
        // Ventilation and cable slots.
        for (x = [45, 65, 85, 105, 125, 145])
            slot(x, 65, 8, 4, 20);
    }
}

module sensor_mast() {
    difference() {
        union() {
            rounded_box([42, 42, 6], 5);
            translate([11, 11, 6]) cylinder(h = 160, d = 20);
            translate([5, 5, 153]) rounded_box([32, 32, 8], 4);
            // Horizontal sensor arm, with a 20 mm clamp seat.
            translate([21, 11, 130]) rotate([0, 90, 0]) cylinder(h = 90, d = 16);
        }
        for (x = [10, 32])
            for (y = [10, 32])
                bolt_hole(x, y, 4.4, 20);
        translate([21, 11, -2]) cylinder(h = 174, d = 12);
        translate([21, 11, 130]) rotate([0, 90, 0]) cylinder(h = 100, d = 8);
    }
}

module wheel_hub() {
    difference() {
        cylinder(h = 18, d = 78);
        translate([0, 0, -2]) cylinder(h = 24, d = wheel_axle_d + 0.4);
        for (a = [0, 90, 180, 270])
            rotate([0, 0, a]) translate([25, 0, -2]) cylinder(h = 24, d = 5.2);
    }
}

module cable_clip() {
    difference() {
        rounded_box([24, 16, 8], 3);
        translate([12, 8, 3]) rotate([90, 0, 0]) cylinder(h = 20, d = 8);
        translate([5, 8, -2]) cylinder(h = 20, d = 4.4);
        translate([19, 8, -2]) cylinder(h = 20, d = 4.4);
    }
}

module print_plate_1() {
    // The two structural halves, 5 mm clearance at the joint.
    translate([0, 0, 0]) base_half(true);
    translate([135, 0, 0]) base_half(false);
}

module print_plate_2() {
    // Four suspension arms and four towers.
    translate([0, 0, 0]) swing_arm();
    translate([110, 0, 0]) swing_arm();
    translate([0, 52, 0]) swing_arm();
    translate([110, 52, 0]) swing_arm();
    translate([0, 100, 0]) suspension_tower();
    translate([52, 100, 0]) suspension_tower();
    translate([104, 100, 0]) suspension_tower();
    translate([156, 100, 0]) suspension_tower();
}

module print_plate_3() {
    // Electronics tray, mast, hubs and cable clips.
    translate([0, 0, 0]) electronics_tray();
    translate([190, 0, 0]) sensor_mast();
    for (i = [0:3])
        translate([(i % 2) * 88, 145 + floor(i / 2) * 88, 0]) wheel_hub();
    for (i = [0:7])
        translate([190 + (i % 4) * 25, 52 + floor(i / 4) * 24, 0]) cable_clip();
}

module print_layout() {
    // Preview all three print plates, separated along Y.
    translate([0, 0, 0]) print_plate_1();
    translate([0, 230, 0]) print_plate_2();
    translate([0, 460, 0]) print_plate_3();
}

if (part == "layout") print_layout();
if (part == "plate") {
    if (plate == 1) print_plate_1();
    if (plate == 2) print_plate_2();
    if (plate == 3) print_plate_3();
}
if (part == "base_left") base_half(true);
if (part == "base_right") base_half(false);
if (part == "swing_arm") swing_arm();
if (part == "tower") suspension_tower();
if (part == "electronics_tray") electronics_tray();
if (part == "mast") sensor_mast();
if (part == "wheel_hub") wheel_hub();
if (part == "cable_clip") cable_clip();
