# K2 Plus with a CFS: first run, step by step

The K2 Plus config has run on a real K2 Plus without a CFS (see
[K2-PLUS-BETA.md](K2-PLUS-BETA.md)). The CFS code has run for months on a
K2 Combo, but never on a K2 Plus: the chute, brush and cutter are in
different places on the Plus, and the Plus positions come from Creality's
stock K2 Plus `box.cfg` (listed in `tools/k2plus/make_k2plus_cfg.py`), not
from a printer we have watched. One K2 position left on the Plus has
already been found and fixed (PA_CAL_RUN ran into the chute wall, issue
#23), so this guide checks every CFS move on its own before it is used for
real, from the least risky to the most.

Do this after K2-PLUS-BETA.md steps 1 to 6 have passed (install, heaters,
homing, `CHUTE_POSITION_CHECK`, Z and the bottom switches). Go one step at
a time, stay next to the printer with your hand near the power switch, and
send back what each step asks for before going on to the next one.

**Stop at once** (power switch, or `M112` in the console) if the toolhead
or bed is about to hit something, a motor grinds, buzzes or keeps pushing,
filament is crushed or tangled, or anything smells hot. Then send the
console output and `klippy.log` (Mainsail > Machine > logs).

Uninstalling puts the printer back to stock at any point:

    sh /mnt/UDISK/k2-mainline-20260729-v62/uninstall.sh

## What to have ready

- Two spools of the same PLA in the **same colour** (for the runout test in
  step 10), and one PLA in a **different colour** (for step 9). Creality
  RFID spools are easiest; others work too.
- Something to cut filament with (step 10).
- Phone for photos and short videos. A video with sound is the best report
  for every step that moves.

## What every CFS move does on the K2 Plus

All positions in mm. The X travel is -10 to 352.5, Y -6.2 to 400.

| Move | Where (K2 Plus) | Speed |
|---|---|---|
| Safe park (start of a cut, end of a purge) | X225 Y345 | `purge_speed` 300 mm/s |
| Purge route into the chute | Y345, then X139, then Y378, then X135, then X133 | 300 mm/s |
| Purge spot over the chute | X133 Y378 | |
| Wipe | over the chute X135 to X160 at Y378, a 4 mm circle on each half of the brush (X160 to X170, Y370 to Y378), three passes back from X160 Y374 to the chute | `clean_speed` 200 mm/s |
| Cutter staging | Y200 (straight forward from the park), then X10 | `cutter_speed` 200 mm/s |
| Cut | ram to X-7.2 (your printer's value, see step 5) at 500 mm/s, back to X-1.7 | |
| Leaving the cutter | from X-1.7 Y200 straight back along the left side to Y345, then X139, Y378, X135, X133 (into the chute) | 200 mm/s |

Nothing in the CFS code moves Z except a small lift before a tool change
(2 mm over the print) and the climb to `purge_min_z` (4.5 mm) when the
bed is closer than that. The chute and brush are fixed to the frame, so the
bed only has to be out of the way.

## Slow mode for the first moves

Steps 3 to 5 run the real CFS routes. Slow them down first: in Mainsail >
Machine, open `printer-overrides.cfg` and add at the end

    [k2_cfs]
    purge_speed: 50
    cutter_speed: 50
    clean_speed: 50
    clean_circle_speed: 50
    clean_accel: 2000

then **Save & Restart**. (If `printer-overrides.cfg` already has a
`[k2_cfs]` section, add the lines to that one.) Step 6 tells you when to
take them out again. They do not change the cut itself, which always rams
at 500 mm/s.

## Step 1: the CFS link (no motion)

Empty bed, nothing printing. In the Mainsail console:

    CFS_STATUS

It asks the box everything it can answer and changes nothing. You should
see `CFS addr 1` and lines for `BOX_STATE`, `MEASURING`, `BOX_MODE`,
`BUFFER_STATE`, `REMAIN_LEN`, `FILAMENT_SENSOR`, `HARDWARE_STATUS`,
`VERSION_SN` and `MEASURING_WHEEL`, without `error`. `VERSION_SN` shows the
CFS firmware (the developer's CFS runs 1.5.0). `BOX_MODE` should say
`idle`.

Also check the start-up line: the console must **not** say `No CFS found:
printing from the rear spool holder`. If it does, check the CFS cable and
power, restart, and send the console output. Then:

    MOTOR_FAULTS

(the CFS shares the motor bus: it should still list X, Y, Z, Z1 and E with
no errors). Leave the printer idle for 5 minutes and run `CFS_STATUS` again:
the CFS light/screen should stay white (not red, which means "host not
talking"), and both outputs should look the same.

Send: both `CFS_STATUS` outputs, `MOTOR_FAULTS`, the CFS firmware version.

## Step 2: slots, RFID and filament records (no motion)

Put a spool in each slot you will use (A, B, C), feeding each one in as
far as the slot pulls it (the CFS feeds a little to its hub sensor by
itself: that is normal, stock does the same). Then:

    CFS_QUERY CMD=FILAMENT_SENSOR
    CFS_RFID_READ
    CFS_SLOTS

`FILAMENT_SENSOR` shows `slots_loaded` with `True` for each slot that has
filament. `CFS_RFID_READ` reads the RFID records the box already holds
(it does not spin the slots): a Creality spool shows material, colour and
remaining %, other spools `no record`. `CFS_SLOTS` opens the slot dialog
(also on Mainsail's CFS panel and the touchscreen) with the filament
record of each slot. For a spool without RFID, set it there, or:

    CFS_SLOT_SET SLOT=B NAME="Generic PLA" TYPE=PLA COLOR=FFFFFF

Take a spool out of a slot and put it back: Mainsail's CFS panel and
`CFS_QUERY CMD=FILAMENT_SENSOR` should follow within a few seconds.

Send: the three outputs, and a photo of Mainsail's CFS panel.

## Step 3: the purge route and the chute (motion, no extrusion)

Slow mode on (see above). Toolhead empty, nozzle cold. Home first:

    G28
    G90
    G1 Z10 F600

Then each command on its own; wait for it to finish and look before the
next:

    CFS_PURGE_POSITION CONFIRM=1

The head goes back to Y345, right or left to X139, back to Y378, then a
little left to X133 over the purge chute, and the console says `CFS purge
position reached: X133.000 Y378.000`. Look from behind and from the side:
the nozzle must be over the chute opening, nothing touched on the way in
(listen for a click or scrape at X139 to X133: that is where the chute flap
is), and the head did not hit the back of the frame at Y378.

    CFS_PURGE_RETURN CONFIRM=1

Back to Y345, then right to X225 (`CFS purge return reached: X225.000
Y345.000`).

Send: a video of both moves, and a photo of the nozzle over the chute
(close up, from the back if you can).

## Step 4: the wipe on the brush (motion, no extrusion)

Still slow mode, cold nozzle, Z10:

    CFS_PURGE_POSITION CONFIRM=1
    NOZZLE_WIPE CONFIRM=1

The wipe: right along the chute to X160 and back to X135 (both at Y378),
a circle on the left half of the brush, a circle on the right half, three
passes from the brush back to the chute, then out to the park at X225 Y345
(`K2 nozzle wipe complete; parked at X225.000 Y345.000`).

Watch that the nozzle drags through the silicone brush on both circles
(not in front of it, not behind it, not on its metal holder) and that the
swings along the chute stay inside the chute's travel. Two things here are
new on the Plus and have never run anywhere: the first swing runs 4 mm
further back (Y378) than the brush line (Y374), and the circles reach from
Y370 to Y378. Tell me exactly where the nozzle went if it was off.

Send: a video of the whole wipe, close to the brush.

## Step 5: the cutter, approach and lever (no cutting)

Toolhead empty: check with

    QUERY_FILAMENT_SENSOR SENSOR=filament_sensor

which must say `Filament Sensor filament_sensor: filament not detected`.
If filament is in the toolhead, do not run this step: do step 6 first (or,
if no CFS slot owns it, unload it as on stock and come back).

First the staging point (the cut always starts there):

    CFS_CUTTER_POSITION CONFIRM=1
    CFS_CUTTER_SENSOR

`CFS_CUTTER_POSITION` needs the head at the park, X225 Y345, where step 4
left it (if it refuses, run `CFS_PURGE_POSITION CONFIRM=1` and then
`CFS_PURGE_RETURN CONFIRM=1` to get there). It moves straight forward to Y200, then left to
X10, and stops there. Look at the left side of the toolhead: the cutter
lever should be lined up with the stop on the left of the frame it is
pushed against, a few mm clear of it. `X10` is the K2's staging X kept on
the Plus (only `Y200` is from the Plus `box.cfg`), so this is one of the
two positions to look at closely. `CFS_CUTTER_SENSOR` must say
`logical=1` (knife returned).

Then the lever, slowly, with nothing to cut:

    CFS_CUTTER_CALIBRATE CONFIRM=1

The head creeps left from X10 to X-5 at 5 mm/s, then 0.05 mm at a time
until the knife sensor trips (no further than X-9.5), backs off to X-1.7
and returns to X10. You should see the lever pushed in and spring back. The
console says where the sensor tripped, whether that is inside the stock
range (-5.5 to -9.5), and how far the cut position goes past it. If it
says `Knife sensor never tripped`, the head is not pressing the lever
(wrong Y, or a toolhead whose sensor never changes): send a photo of the
lever at the staging point and stop here.

    CFS_CUTTER_SENSOR

The sensor must be back to `logical=1`. Now the way a real cut leaves the
cutter, slowly by hand: after the cut the head stays at X-1.7 and goes
straight back along the left side of the printer to Y345 before it turns
right to the chute. Nothing has ever run there on a Plus:

    G1 X-1.7 F600
    G1 Y345 F1200
    G1 X139 F1200
    G1 Y378 F1200
    G1 X133 F1200
    CFS_PURGE_RETURN CONFIRM=1

Watch the left side all the way from Y200 to Y345: the toolhead and the
cutter lever must not touch the frame, a cable or the side panel.

Also send the line from the installer output (step 2 of K2-PLUS-BETA.md)
that starts `extruder:` (it shows `cutter depth X...` and, if found,
Creality's own calibration from the stock config). The cut uses that depth
(`cutter_cut_x`).

Send: the console output, a video of the calibration with sound, a photo
of the lever at the staging point.

## Step 6: one cut and unload

Take the slow mode lines out of `printer-overrides.cfg` and Save & Restart:
from here the moves run at their real speeds, and the cut needs the full
ram speed anyway. Home again (`G28`) after the restart.

This step needs a CFS slot's filament already in the toolhead: if the
printer came from stock with a CFS slot loaded, `QUERY_FILAMENT_SENSOR
SENSOR=filament_sensor` says `filament detected` and `CFS_STATUS` shows
that slot as `active_slot` in `MEASURING`. If nothing is loaded, do step 7
first and then come back here.

With slot A loaded (use your slot letter):

    G90
    G1 Z10 F600
    CFS_PURGE_POSITION CONFIRM=1
    M109 S220
    CFS_PURGE_RETURN CONFIRM=1
    CFS_CUT_UNLOAD SLOT=A CONFIRM=1

The nozzle heats over the chute, the head goes to the park, then:
retracts 2 mm, goes to the cutter (Y200, X10), **rams the lever** (a sharp
click), comes back to X-1.7, pushes and pulls the tip, goes back along the
left side and into the chute (the path you checked in step 5), and the CFS
pulls the filament back: three pulls and one slow
one at the extruder, then the CFS rewinds it into slot A (this takes up to
a minute; you hear the CFS motor). It ends with `CFS slot A unload
complete; nozzle sensor is clear`.

Then:

    CFS_FEED_STOP
    CFS_PRELOAD ENABLE=1
    M104 S0
    CFS_STATUS

Look at the cut end of the filament if you can see it at the slot (it
should be a clean, flat cut, not a squashed or stretched end). If the
console says the CFS could not pull the filament back, or a strand is left
in the extruder, the cut is incomplete: stop and send the output (the fix
is a deeper `cutter_cut_x`, see INSTALL.md "Troubleshooting").

Send: the console output, a video with sound from the cut to the end of
the rewind, a photo of the cut filament end.

## Step 7: one load and purge

Slot A has filament at its inlet (step 2), the toolhead is empty.

    G90
    G1 Z10 F600
    CFS_PURGE_POSITION CONFIRM=1
    M109 S220
    CFS_LOAD SLOT=A CONFIRM=1

The CFS feeds slot A through the hub to the toolhead (about 20 s and you
hear it), the extruder takes it when the nozzle sensor sees it, and it
ends with `CFS slot A load complete; nozzle sensor is triggered`. Nothing
comes out of the nozzle yet beyond a few mm.

    CFS_PURGE CONFIRM=1

The purge over the chute: two 90 mm blocks of fast pushes with short
pauses, the fan blasts so the strand snaps off into the chute, a
retract, and a wipe after each block. Watch where the purge strand goes:
it must fall into the chute, not curl onto the brush, the bed or the
toolhead. On the Plus the purge spot (X133) is only 2 mm from the chute's
left edge (X135); on the K2 it was 12 mm. Then:

    NOZZLE_WIPE CONFIRM=1
    CFS_FEED_STOP
    CFS_PRELOAD ENABLE=1
    M104 S0

Now the same thing the way the screen does it, which runs steps 6 and 7
in one go (it cuts and rewinds the loaded slot first, then loads B and
purges, then wipes while cooling):

    CFS_LOAD_SLOT SLOT=B

and back:

    CFS_LOAD_SLOT SLOT=A

Send: the console output, a video of the purge with the chute in view, a
photo of what ended up in the chute (and anywhere else).

## Step 8: a short single-colour print (feed and take-up)

Slice something small and low in PLA from slot A (for example a 20 mm cube
at 0.2 mm, about 15 minutes) with the K2 Plus OpenKlipper profile set up as
in INSTALL.md step 6. Start it from Mainsail or the screen. START_PRINT
homes, loads and purges slot A over the chute, wipes, measures Z and
prints. During the print the CFS refills the
buffer in short bursts (about 25 to 30 mm each) as the extruder uses
filament: listen for the CFS motor running for a second or two every so
often. Between layers you can check it with

    CFS_QUERY CMD=BUFFER_STATE

(`slack`, `mid` or `tensioned`; it should move between them, never stay
`slack` for long). Watch for under-extrusion that gets worse over the
print (the CFS not keeping up) or the filament between the CFS and the
toolhead going taut and pulling the head.

After the print the slot stays loaded. Send: the console output of the
whole print, a photo of the print, and `CFS_SLOT_STATS`.

## Step 9: a two-colour print (tool changes)

Slots A and B with two different PLA colours. Slice a small two-colour
object (for example a 20 mm cube with the top half in the second colour,
so only a couple of changes; prime tower on, Orca's settings from
INSTALL.md step 6). In Orca, sync the filaments from the printer first.

Every change: the head lifts 2 mm, moves back to Y345 and across to X225,
forward to the cutter and cuts, into the chute, rewinds the old slot,
loads the new one, purges (the length from the file's flush volumes),
wipes, and goes back over the print and down. The console ends each one
with `CFS tool change complete: slot A to slot B in ...s (cut/unload ...,
load ..., purge ..., return ...)`.

Watch the first change from start to end. Stop the print (Mainsail cancel,
or the power switch if the head is about to hit the print) if the head
crosses the print without lifting clear of it. If a change fails, the
print pauses at the chute and the console says why: send that output
before trying `RESUME` (which tries the change again).

Send: the console output, a video of the first tool change, a photo of
the print (colour bleed at the change, blobs on the print, a clean prime
tower).

## Step 10: runout and automatic refill

Slots A and C with the **same** filament (same material and colour in
their records: check with `CFS_SLOTS`; the refill only picks a slot that
matches). Start the print from step 8 again, from slot A. Once it is
printing the walls, cut the filament of slot A **between the spool and the
CFS inlet** and let the loose end run in.

What should happen, in order:

1. Within a few seconds the console says `Slot A is running out. Slot C
   (same filament) takes over automatically when it does.`
2. The print keeps going until the tail passes the toolhead sensor (a
   minute or more: the tube to the head still holds filament).
3. The print pauses, the head lifts 2 mm and parks at X225 Y345, and the
   console says `Slot A ran out: refilling from slot C (same material and
   colour)`.
4. The head goes into the chute, pushes the old tail through (30 mm), the
   CFS loads slot C, a short purge (40 mm), a wipe, and the print resumes:
   `Runout: refilled from slot C, resuming the print`.

If no slot matches, or the refill fails, the print stays paused at the
chute and says so: put a new spool in and `RESUME` (it loads and purges
first). Send that output too.

Send: the console output from the cut to the resume, a video of the
refill, a photo of the print around the resume (a gap or blob there is
worth reporting).

## Positions to check at the printer

These come from the static check of every CFS move on the K2 Plus (all
inside the Plus travel, and all from the stock K2 Plus `box.cfg` where the
generator says so). The ones not confirmed for the Plus:

- **Cutter staging X10** (`cutter_x`) is the K2 value: the Plus
  `box.cfg` gives the cutter row (Y200), not this X. Step 5.
- **Cut depth X-7.2 and release X-1.7** are the K2 stock values unless the
  installer found Creality's own `cut_pos_x` calibration in the stock
  config. Step 5 (calibration) and step 6 (the cut).
- **The way back from the cutter** runs at X-1.7 from Y200 to Y345, close
  to the left side of the frame (on the K2 it was Y150 to Y261). Step 5.
- **The first wipe swing at Y378** (the purge spot's Y) and the circles
  from Y370 to Y378: on the K2 the chute and the brush line had the same Y,
  so this has never run. Step 4.
- **The purge spot X133**, 2 mm inside the chute's left edge. Step 7.
- **Pause and tool-change park X225 Y345** is on the back of the bed on the
  Plus: a tall print in the back right corner is in the way of the 2 mm
  lift. Avoid that corner for steps 8 to 10.
- `stall_retry_moves` (Y340, X300, Y40, X40, the sweep that frees a stuck
  PTFE tube) and `clean_brush_strokes` are off by default and stay off for
  this test.

## What to send back after each step

- the console output of the step
- `klippy.log` (Mainsail > Machine > logs), after any problem
- the photos and videos the step asks for

Thank you for testing.
