# ICBM Port for Ford Vehicles - Implementation Summary

## Overview

This document summarizes the work done to port Intelligent Cruise Button Management (ICBM) functionality from the `ICBM-WIP` branch into `bp-6.0-wip`. ICBM allows openpilot to control cruise speed by emulating button presses, which is particularly useful for Ford vehicles where openpilot longitudinal control is available but many drivers prefer not to use it due to jerkiness.

## Branch Information

- **Source Branch**: `origin/ICBM-WIP`
- **Target Branch**: `bp-6.0-wip`
- **Date**: February 5, 2026
- **Commits Merged**: `06c76f3028ca26ad4f7c05673ba32f93135a9b7d` through `df1751e3eec2d66cd9abc36b8dc73c246422c2d8`

## How ICBM and SLA Actually Work (As Designed)

After syncing with the latest upstream sunnypilot, the system is now working as intended. Here's how each component functions:

### ICBM (Intelligent Cruise Button Management)
- **Function**: ICBM can control the steering wheel buttons to increase and decrease speed setpoint
- **Status**: ✅ **This always worked for us**
- **How it works**: ICBM reads the target speed from the longitudinal planner and sends button press commands via CAN to adjust the cluster cruise setpoint

### Smart Cruise Control (SCC)
- **Function**: Smart Cruise Control Vision (SCC-V) or Map (SCC-M) will slow down your speed setpoint in curves
- **Status**: ✅ **This always worked for us**
- **How it works**: SCC analyzes curves ahead using vision or map data and provides a lower target speed to the longitudinal planner, which ICBM then adjusts the cluster setpoint to match

### Speed Limit Assist (SLA)
- **Function**: Speed Limit Assist changes the set speed based on speed limit signs, **but prompts the user to accept the change with either the up or down button**
- **Status**: ✅ **This was working for us in the end** (after fixes)
- **How it works**: 
  - When a speed limit change is detected, SLA enters `preActive` state
  - SLA provides the new target speed to the longitudinal planner
  - The user must press the up (+) or down (-) button to accept the change
  - Once accepted, SLA transitions to `active` state and ICBM adjusts the cluster setpoint to match

### Important Clarification: SLA Does NOT Auto-Adjust Without User Confirmation
- **Function**: SLA does **NOT** support automatically changing speed setpoint based on speed limit signs **without user confirmation**
- **Status**: ❌ **This explains why we couldn't get it to work automatically**
- **Why**: This is by design - SLA requires user confirmation via button press to prevent unexpected speed changes. The system is working correctly when it prompts for user confirmation.

### Summary
The confusion during development was trying to make SLA automatically adjust speeds without user confirmation, which is not how it's designed to work. The system is functioning correctly when:
1. ICBM adjusts speeds based on SCC-V/M targets (curves) - ✅ Automatic
2. SLA prompts user to accept speed limit changes - ✅ Requires button press (by design)
3. ICBM adjusts cluster setpoint after user accepts SLA change - ✅ Works after confirmation

## Key Difference: PCM-Capable vs Non-PCM Cars

### Important Context

**ICBM was originally designed for non-PCM cars** (cars that CANNOT use openpilot longitudinal control):
- These cars have `pcmCruiseSpeed = True` by default
- SLA is **disabled** on these cars (line 37-38 in `helpers.py`)
- ICBM exits early if `pcmCruiseSpeed = True` (line 117-118 in `controller.py`)
- **Result**: ICBM runs, but SLA is disabled. They never interact, so no circular dependency issue.

**Ford vehicles are PCM-capable** (CAN use openpilot longitudinal control):
- Many Ford drivers choose ICBM instead of OP long because OP long is jerky
- When ICBM is enabled, `pcmCruiseSpeed = False` (set by line 64 in `interfaces.py`)
- SLA is **enabled** because `pcmCruiseSpeed = False`
- ICBM runs because `pcmCruiseSpeed = False`
- **Result**: Both SLA and ICBM run and need to coordinate, creating a circular dependency that requires a fix.

### Why the SLA Dependency Fix is Needed for Fords

The upstream repository owner is correct that the fix isn't needed for non-PCM cars (where SLA is disabled). However, **the fix IS needed for PCM-capable cars like Fords** that choose to use ICBM instead of OP long, because:

1. Both SLA and ICBM are enabled and running
2. SLA needs to provide target speed to ICBM
3. ICBM needs the target to adjust cluster setpoint
4. SLA won't provide target until cluster matches (circular dependency)
5. The fix breaks this dependency by allowing SLA to provide target in `preActive` state when ICBM is available

## Changes Merged

### 1. CarState Integration (`opendbc_repo/opendbc/car/ford/carstate.py`)

**Changes Applied:**
- Added `CarStateExt` import and inheritance
- Integrated ICBM button event tracking
- Added cruise state validation: `if(ret.cruiseState.speed == 0): ret.cruiseState.available = False`
- Added `CarStateExt.update()` call in `update()` method
- Added `*self.button_events` to buttonEvents list

**Files Already Present:**
- `opendbc_repo/opendbc/sunnypilot/car/ford/carstate_ext.py` - ICBM button tracking logic
- `opendbc_repo/opendbc/sunnypilot/car/ford/values_ext.py` - Button definitions

### 2. ICBM Availability Flag (`opendbc_repo/opendbc/car/ford/interface.py`)

**Critical Fix:**
- Changed `intelligentCruiseButtonManagementAvailable` from `False` to `True`
- This flag was intentionally disabled for testing but needed to be re-enabled
- Without this flag set to `True`, ICBM never initializes, causing state to remain at 0 (inactive)

**Code Location:** Line 139
```python
ret.intelligentCruiseButtonManagementAvailable = True
```

### 3. SLA/ICBM Circular Dependency Fix (`sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_assist.py`)

**The Problem:**
- SLA only provided target speed when `is_active = True` (in `active` or `adapting` states)
- SLA enters `preActive` state when speed limit changes
- ICBM needs the target to adjust cluster setpoint
- But SLA won't become `active` until cluster matches target
- **Circular dependency**: ICBM can't adjust without target, but SLA won't provide target until adjusted

**The Solution:**
Added logic to allow SLA to provide target in `preActive` state when ICBM is available:

```python
# ICBM vehicles also get target in preActive state to break circular dependency
if (not self.pcm_op_long and self.is_enabled and
    self.state == SpeedLimitAssistState.preActive and
    self.CP_SP.intelligentCruiseButtonManagementAvailable):
  return self._speed_limit_final_last
```

**Code Location:** Lines 141-145 in `speed_limit_assist.py`

**Why This Works:**
- Breaks the circular dependency: SLA preActive → provides target → ICBM adjusts cluster → SLA becomes active
- Only applies when ICBM is available (`intelligentCruiseButtonManagementAvailable = True`)
- Non-PCM cars don't need this because SLA is disabled on those cars anyway

**Note:** This fix allows SLA to provide the target, but SLA still requires user confirmation via button press to transition to `active` state. The fix ensures ICBM can see the target and adjust the cluster, but the user must still press a button to accept the speed limit change (as designed).

## Commits Merged

The following commits from `ICBM-WIP` were incorporated:

1. `06c76f3028ca26ad4f7c05673ba32f93135a9b7d` - Base commit
2. `2139dc1124761ec247f11955affa22dbc163da1a` - Updated button mappings
3. `92ba0061f58cd20cf4109e54f005b14eacbb073a` - tmux logging to debug button presses
4. `ec82fad6042196d3513b3a84973361a5392986da` - change button type messaging away from raw
5. `57ccfffe05541358011464a41d921475c913b474` - remove cruise stat debugging messages
6. `95cb04885657edba52a39751aeba69486f97334c` - smart mapping of combo buttons v1
7. `e9b48b5862ea44452fda107b3a55153248d45c55` - decrease button also acts as set if the cruise is deactivated
8. `76550aef35519936b0f8adae143ae61a9f361ee4` - try to fix cancel/resume glitch
9. `bdba7e7598c711da538e34ab8957f5b262d555c9` - set cruise target with Main-On toggle
10. `6e62b5f263d963f0b2722b1d2ea3bdff249ed9fa` - Fix button not release properly (hopefully)
11. `df1751e3eec2d66cd9abc36b8dc73c246422c2d8` - Fix ICBM/SLA circular dependency for Ford vehicles

## Files Modified

### Core ICBM Files (Already Present)
- `opendbc_repo/opendbc/sunnypilot/car/ford/carstate_ext.py` - Button event tracking
- `opendbc_repo/opendbc/sunnypilot/car/ford/values_ext.py` - Button definitions
- `opendbc_repo/opendbc/sunnypilot/car/ford/icbm.py` - Ford-specific ICBM interface

### Files Modified During Port
- `opendbc_repo/opendbc/car/ford/carstate.py` - Added CarStateExt integration
- `opendbc_repo/opendbc/car/ford/interface.py` - Enabled ICBM availability flag
- `sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_assist.py` - Added circular dependency fix

## Testing Notes

### Initial Issue Found
- `carControlSP.intelligentCruiseButtonManagement.state` was 0 (inactive) for entire drive
- Root cause: `intelligentCruiseButtonManagementAvailable` was `False`
- Fix: Changed to `True` in Ford interface

### Expected Behavior After Fix
- ICBM should initialize and run when:
  - `IntelligentCruiseButtonManagement` param is enabled (user toggle)
  - `CP_SP.intelligentCruiseButtonManagementAvailable = True` ✅ (fixed)
  - `not CP.openpilotLongitudinalControl` (not using OP Long)
  - ICBM readiness conditions are met (longitudinal enabled, no overrides, no button presses)

### ICBM State Values
- `0` = inactive (not ready)
- `1` = preActive (ready, waiting 0.4s)
- `2` = increasing (sending increase buttons)
- `3` = decreasing (sending decrease buttons)
- `4` = holding (target matches cluster)

---

# Speed Limit Assist (SLA) with Intelligent Cruise Button Management (ICBM) - Technical Documentation

## Overview

This document describes how Speed Limit Assist (SLA) operates when coupled with Intelligent Cruise Button Management (ICBM) on Ford vehicles. Understanding this interaction is critical for debugging why SLA may not always adjust the cluster setpoint as expected.

## Key Concepts

### Longitudinal Control Methods

There are two primary ways the Comma3X can achieve longitudinal control:

1. **OP Long (Openpilot Longitudinal Control)**: The Comma3X software directly sends gas and brake signals to the vehicle. The cluster setpoint is typically set to a high value (70-80 mph) and remains static while OP Long controls actual speed.

2. **ICBM (Intelligent Cruise Button Management)**: The OEM Adaptive Cruise Control (ACC) handles gas and brake, while ICBM sends cruise button commands to adjust the cluster setpoint based on Comma3X logic. This allows limited longitudinal control without direct gas/brake access.

### Speed Limit Assist (SLA)

SLA automatically adjusts vehicle speed to match detected speed limits from:
- Map database (via `liveMapDataSP`)
- OEM vehicle camera (read over CAN bus)

SLA operates differently depending on which longitudinal control method is active.

## SLA Behavior: OP Long vs ICBM

### SLA with OP Long (`pcm_op_long = True`)

**Target Setpoint Behavior:**
- SLA sets `target_set_speed_conv` to `PCM_LONG_REQUIRED_MAX_SET_SPEED`:
  - Imperial: 70 mph (low threshold) or 80 mph (high threshold)
  - Metric: 120 km/h (low threshold) or 130 km/h (high threshold)
- The threshold selection depends on whether the speed limit is below the Confirm Speed Threshold (CST):
  - Below CST (50 mph / 80 km/h): Uses low threshold (70 mph / 120 km/h)
  - At/Above CST: Uses high threshold (80 mph / 130 km/h)

**User Interaction:**
- User must manually set cruise control to the required threshold (70 or 80 mph)
- Once set, SLA becomes active and controls actual vehicle speed via gas/brake signals
- The cluster setpoint remains at 70/80 mph while actual speed matches the speed limit
- Example: Cluster shows 70 mph, but vehicle cruises at 45 mph (the detected speed limit)

**State Machine:**
- Uses `update_state_machine_pcm_op_long()`
- Requires user confirmation to set cluster to threshold speed
- Once confirmed, SLA controls speed via direct gas/brake commands

### SLA with ICBM (`pcm_op_long = False`)

**Target Setpoint Behavior:**
- SLA sets `target_set_speed_conv` directly to the detected speed limit (`speed_limit_final_last_conv`)
- No high threshold is used - the target is the actual speed limit value
- Example: If speed limit is 45 mph, `target_set_speed_conv = 45 mph`

**System Interaction:**
- ICBM reads the target speed from `longitudinalPlanSP.vTarget`
- SLA provides this target when `is_active = True` (in `active` or `adapting` state) OR when in `preActive` state with ICBM available (fix applied)
- ICBM compares `v_target` (from longitudinal plan) with `v_cruise_cluster` (current cluster setpoint)
- ICBM sends button presses to adjust cluster setpoint to match the target

**State Machine:**
- Uses `update_state_machine_non_pcm_long()`
- Different logic flow than OP Long mode
- Does not require manual user confirmation in the same way

## SLA State Machine (ICBM Mode)

The SLA state machine for ICBM mode (`update_state_machine_non_pcm_long()`) operates as follows:

### SLA States

1. **`disabled`**: SLA is not enabled or longitudinal control is not engaged
2. **`inactive`**: SLA is enabled but no speed limit is detected or conditions not met
3. **`preActive`**: Waiting for cluster setpoint to match target speed limit
4. **`active`**: Cluster setpoint matches speed limit, cruising at speed limit
5. **`adapting`**: Reducing speed to match new lower speed limit
6. **`pending`**: Awaiting new speed limit detection

### State Transitions (ICBM Mode)

**From `disabled`:**
- Transitions to `preActive` when:
  - `long_enabled = True` AND `enabled = True`
  - Guard timer (`DISABLED_GUARD_PERIOD = 0.5s`) expires
  - Speed limit is available OR cluster already matches target

**From `preActive`:**
- Transitions to `active` when:
  - `target_set_speed_confirmed = True` (cluster matches target)
  - This is checked via `_update_non_pcm_long_confirmed_state()`
- Transitions to `inactive` when:
  - `pre_active_timer` expires (timeout: 5 seconds for ICBM mode)
  - This timeout prevents indefinite waiting

**From `active`:**
- Transitions to `inactive` when:
  - User manually changes cluster setpoint (`v_cruise_cluster_changed = True`)
- Transitions to `preActive` when:
  - Speed limit changes AND `apply_confirm_speed_threshold = True`
  - **Important**: This only happens if the new speed limit is below the Confirm Speed Threshold (50 mph / 80 kmh)
  - **Limitation**: If speed limit increases from above-CST to above-CST (e.g., 50→60 mph), SLA will NOT transition to preActive and will NOT increase the setpoint automatically

**From `inactive`:**
- Transitions to `preActive` when:
  - Speed limit changes
  - Cluster setpoint matches target (`_update_non_pcm_long_confirmed_state()` returns True)

### Critical Function: `_update_non_pcm_long_confirmed_state()`

This function determines if the cluster setpoint matches the target:

```python
def _update_non_pcm_long_confirmed_state(self) -> bool:
    if self.target_set_speed_confirmed:
        return True

    if self.state != SpeedLimitAssistState.preActive:
        return False

    req_plus, req_minus = compare_cluster_target(
        self.v_cruise_cluster,
        self._speed_limit_final_last,
        self.is_metric
    )

    return self._get_button_release(req_plus, req_minus)
```

**Key Points:**
- Returns `True` if cluster already matches target (`target_set_speed_confirmed`)
- If in `preActive` state, checks if user recently released a button that would have adjusted speed
- Uses `_get_button_release()` to detect if user manually pressed buttons within the last 0.5 seconds
- This prevents SLA from activating if user is manually adjusting speed

## Speed Limit Offset Handling

**Important**: SLA includes the speed limit offset when calculating the target setpoint.

### How Offset is Applied

1. **Offset Calculation** (`SpeedLimitResolver._get_speed_limit_offset()`):
   - **Fixed Offset**: Adds/subtracts a fixed value (e.g., +5 mph)
   - **Percentage Offset**: Adds/subtracts a percentage of the speed limit (e.g., +10% of 50 mph = +5 mph)
   - **Off**: No offset applied

2. **Final Speed Limit** (`SpeedLimitResolver.update_speed_limit_states()`):
   ```python
   self.speed_limit_final = self.speed_limit + self.speed_limit_offset
   self.speed_limit_final_last = self.speed_limit_final  # When speed_limit > 0
   ```

3. **Target Setpoint** (`SpeedLimitAssist.update_calculations()`):
   - In ICBM mode: `target_set_speed_conv = speed_limit_final_last_conv`
   - This includes the offset!

### Example

If you're in a **50 mph zone** with a **+5 mph fixed offset**:
- Speed limit detected: 50 mph
- Offset applied: +5 mph
- `speed_limit_final_last` = 55 mph
- `target_set_speed_conv` = 55 mph (in ICBM mode)
- **Cluster must be set to 55 mph** for SLA to transition to `active` state

The `target_set_speed_confirmed` property compares:
```python
v_cruise_cluster_conv == target_set_speed_conv
```

So yes, the cluster setpoint must match the speed limit **plus the offset** for SLA to become active.

### Rounding Behavior

**Important**: Both the target and cluster setpoint are rounded to whole numbers (1 mph/kmh increments) before comparison:

```python
# In update_calculations():
self.speed_limit_final_last_conv = round(self._speed_limit_final_last * speed_conv)
self.v_cruise_cluster_conv = round(self.v_cruise_cluster * speed_conv)
self.target_set_speed_conv = self.speed_limit_final_last_conv  # In ICBM mode

# In target_set_speed_confirmed:
return bool(self.v_cruise_cluster_conv == self.target_set_speed_conv)
```

**Percentage Offset Consideration:**

When using percentage offsets (e.g., +7%), the calculated speed limit may result in a decimal value:
- Example: 50 mph speed limit + 7% = 53.5 mph
- After rounding: `round(53.5)` = **54 mph**
- Cluster must be set to **54 mph** (not 53.5 mph) for SLA to activate

**Potential Issue with Percentage Offsets:**

While rounding should handle decimal values correctly, there are edge cases to consider:

1. **Floating Point Precision**: The percentage calculation (`speed_limit * 1.07`) and subsequent unit conversions (mph ↔ m/s) may introduce floating-point precision errors that could affect rounding.

2. **ICBM Rounding**: ICBM also rounds the target it receives:
   ```python
   # In ICBM controller:
   self.v_target = round(self.v_target_ms_last * speed_conv)
   self.v_cruise_cluster = round(CS.cruiseState.speedCluster * speed_conv)
   ```
   If SLA provides `output_v_target` in m/s with slight precision differences, ICBM's rounding might produce a different integer value than SLA's `target_set_speed_conv`.

3. **Example Edge Case**:
   - Speed limit: 43 mph
   - +7% offset: 43 * 1.07 = 46.01 mph
   - Converted to m/s: 46.01 / 2.23694 ≈ 20.57 m/s
   - SLA rounds: `round(20.57 * 2.23694)` = `round(46.01)` = **46 mph**
   - If ICBM receives slightly different m/s value due to precision: `round(20.58 * 2.23694)` = `round(46.02)` = **46 mph** ✓
   - But if precision error is larger: `round(20.60 * 2.23694)` = `round(46.04)` = **46 mph** ✓
   - However, if the error accumulates: `round(20.65 * 2.23694)` = `round(46.22)` = **46 mph** ✓

**Conclusion**: Rounding should handle percentage offsets correctly in most cases, but floating-point precision errors could theoretically cause mismatches.

**Your Specific Case (+7% offset)**:

With a +7% offset, you're creating decimal speed limits that must round to whole numbers. For example:
- 50 mph + 7% = 53.5 mph → rounds to **54 mph**
- 43 mph + 7% = 46.01 mph → rounds to **46 mph**
- 57 mph + 7% = 60.99 mph → rounds to **61 mph**

The rounding logic should work, but there's a potential issue: **If the speed limit detection fluctuates slightly** (e.g., between 49.9 and 50.1 mph), the percentage calculation could produce values that round differently:
- 49.9 mph + 7% = 53.393 mph → rounds to **53 mph**
- 50.1 mph + 7% = 53.607 mph → rounds to **54 mph**

This could cause ICBM to oscillate between 53 and 54 mph, preventing SLA from ever stabilizing in the `active` state.

**Recommendations**:
- **Use fixed offset** instead of percentage (e.g., +5 mph) to avoid decimal calculations
- **Monitor logs** to see if `speed_limit_final_last_conv` is fluctuating between values
- **Check speed limit detection stability** - if the detected speed limit varies, percentage offsets will amplify the variation
- Consider using a percentage that results in whole numbers (e.g., +10% of 50 mph = 55 mph exactly)

## ICBM Integration Flow

### Data Flow

1. **Speed Limit Detection**:
   - `SpeedLimitResolver` detects speed limits from map or vehicle camera
   - Calculates offset based on user settings (fixed or percentage)
   - Provides `speed_limit_final_last` (speed limit + offset) to SLA

2. **SLA Processing**:
   - SLA receives speed limit and current cluster setpoint (`v_cruise_cluster`)
   - Calculates `target_set_speed_conv` = speed limit (in ICBM mode)
   - Updates state machine based on current conditions
   - Sets `output_v_target` when `is_active = True` OR when in `preActive` state with ICBM available (fix)

3. **Longitudinal Planner**:
   - `LongitudinalPlannerSP` collects targets from multiple sources:
     - Cruise control setpoint
     - SCC-Vision (Smart Cruise Control - Vision)
     - SCC-Map (Smart Cruise Control - Map)
     - SLA (`sla.output_v_target`)
   - Selects minimum target: `self.source = min(targets, key=lambda k: targets[k][0])`
   - Publishes `longitudinalPlanSP.vTarget`

4. **ICBM Processing**:
   - ICBM reads `longitudinalPlanSP.vTarget`
   - Compares with current `v_cruise_cluster`
   - Determines if increase/decrease buttons needed
   - Updates state machine (inactive → preActive → increasing/decreasing → holding)

5. **Button Press Generation**:
   - ICBM sets `sendButton` state (increase, decrease, or none)
   - Ford-specific ICBM interface sends CAN messages with button press signals
   - Button presses are rate-limited (minimum 0.05s between presses)

### ICBM State Machine

ICBM has its own state machine that operates independently from SLA. The ICBM state machine controls when button presses are sent to adjust the cluster cruise setpoint.

#### ICBM States

1. **`inactive` (0)**: ICBM is not ready to operate
   - Occurs when `is_ready = False`
   - Conditions that cause inactive state:
     - Longitudinal control not enabled (`CC.enabled = False`)
     - Cruise control override active (`CC.cruiseControl.override = True`)
     - Cruise control being cancelled (`CC.cruiseControl.cancel = True`)
     - Cruise control being resumed (`CC.cruiseControl.resume = True`)
     - Manual button presses detected (user pressing cruise buttons)
   - No button presses are sent in this state

2. **`preActive` (1)**: ICBM is ready and waiting before starting adjustments
   - Occurs when `is_ready = True` transitions from `False` to `True`
   - Pre-active timer starts at 0.4 seconds (`INACTIVE_TIMER = 0.4s`)
   - Timer counts down each control cycle (20Hz = 0.05s per cycle)
   - After timer expires, ICBM transitions to `increasing`, `decreasing`, or `holding` based on target vs cluster comparison
   - No button presses are sent during the timer countdown

3. **`increasing` (2)**: Sending increase button presses to raise cluster setpoint
   - Occurs when `v_target > v_cruise_cluster` (target speed is higher than current cluster setpoint)
   - ICBM sends increase button presses at rate-limited intervals (minimum 0.05s between presses)
   - Transitions to `holding` when `v_target <= v_cruise_cluster` (target reached or exceeded)
   - Button signal sent: `CcAslButtnSetIncPress` (increase cruise speed)

4. **`decreasing` (3)**: Sending decrease button presses to lower cluster setpoint
   - Occurs when `v_target < v_cruise_cluster` AND `v_cruise_cluster > v_cruise_min` (target is lower, but not below minimum)
   - ICBM sends decrease button presses at rate-limited intervals (minimum 0.05s between presses)
   - Transitions to `holding` when:
     - `v_target >= v_cruise_cluster` (target reached or exceeded), OR
     - `v_cruise_cluster <= v_cruise_min` (reached minimum setpoint)
   - Button signal sent: `CcAslButtnSetDecPress` (decrease cruise speed)

5. **`holding` (4)**: Target matches cluster setpoint, no adjustment needed
   - Occurs when `v_target == v_cruise_cluster` (target equals current cluster setpoint)
   - No button presses are sent in this state
   - Transitions to `preActive` when `v_target != v_cruise_cluster` (target changes)

#### ICBM State Transitions

**From `inactive`:**
- Transitions to `preActive` when:
  - `is_ready` changes from `False` to `True`
  - Pre-active timer is initialized to 0.4 seconds

**From `preActive`:**
- Transitions to `holding` when:
  - Pre-active timer expires (`pre_active_timer <= 0`) AND
  - `v_target == v_cruise_cluster` (target already matches cluster)
- Transitions to `increasing` when:
  - Pre-active timer expires AND
  - `v_target > v_cruise_cluster` (need to increase)
- Transitions to `decreasing` when:
  - Pre-active timer expires AND
  - `v_target < v_cruise_cluster` AND `v_cruise_cluster > v_cruise_min` (need to decrease, but not below minimum)
- Transitions back to `inactive` when:
  - `is_ready` becomes `False` (any readiness condition fails)

**From `holding`:**
- Transitions to `preActive` when:
  - `v_target != v_cruise_cluster` (target changed, need to adjust)
- Transitions to `inactive` when:
  - `is_ready` becomes `False`

**From `increasing`:**
- Transitions to `holding` when:
  - `v_target <= v_cruise_cluster` (target reached or exceeded)
- Transitions to `inactive` when:
  - `is_ready` becomes `False`

**From `decreasing`:**
- Transitions to `holding` when:
  - `v_target >= v_cruise_cluster` (target reached or exceeded), OR
  - `v_cruise_cluster <= v_cruise_min` (reached minimum setpoint)
- Transitions to `inactive` when:
  - `is_ready` becomes `False`

#### ICBM Readiness Conditions

ICBM can only operate when `is_ready = True`. Readiness is determined by:

```python
ready = CC.enabled and not CC.cruiseControl.override and not CC.cruiseControl.cancel and not CC.cruiseControl.resume
button_pressed = any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers)
self.is_ready = ready and not button_pressed
```

**Required conditions:**
- `CC.enabled = True` (longitudinal control enabled)
- `CC.cruiseControl.override = False` (no override active)
- `CC.cruiseControl.cancel = False` (not cancelling cruise)
- `CC.cruiseControl.resume = False` (not resuming cruise)
- No manual button presses detected (all button timers = 0)

**Note**: When SLA is active, the target includes the speed limit offset. For example, if speed limit is 50 mph with +5 mph offset, ICBM will adjust cluster to 55 mph to match the target.

## Speed Limit Increases - Critical Limitation

**Important Discovery**: SLA has a significant limitation when it comes to **increasing** speed setpoints in ICBM mode.

### The Problem

When SLA is in `active` state and the speed limit increases, it will only transition to `preActive` (to allow ICBM to adjust) if:

```python
self.speed_limit_changed and self.apply_confirm_speed_threshold
```

The `apply_confirm_speed_threshold` property returns `True` when:
- Current cluster setpoint is below Confirm Speed Threshold (50 mph / 80 kmh), OR
- New speed limit is below Confirm Speed Threshold

### What This Means

**Example Scenario**:
- Current: 50 mph zone + 5 mph offset = 55 mph setpoint (above CST of 50 mph)
- New: 60 mph zone + 5 mph offset = 65 mph setpoint (above CST of 50 mph)
- **Result**: `apply_confirm_speed_threshold = False` (both are above CST)
- **SLA Behavior**: Does NOT transition to `preActive`, stays in `active` state
- **ICBM Behavior**: Cannot increase setpoint because SLA doesn't provide new target
- **Outcome**: Setpoint remains at 55 mph, does NOT increase to 65 mph

### When Speed Increases DO Work

Speed increases will work automatically only if:
1. Current cluster setpoint is below 50 mph (CST), OR
2. New speed limit is below 50 mph (CST)

**Example that works**:
- Current: 45 mph zone + 5 mph offset = 50 mph setpoint (at CST)
- New: 55 mph zone + 5 mph offset = 60 mph setpoint (above CST)
- **Result**: `apply_confirm_speed_threshold = True` (current is at CST)
- **SLA Behavior**: Transitions to `preActive`, allows ICBM to adjust
- **Outcome**: Setpoint increases from 50 mph to 60 mph ✓

### Code Reference

```python
# Line 321 in speed_limit_assist.py (ICBM mode)
if self.state == SpeedLimitAssistState.active:
    if self.v_cruise_cluster_changed:
        self.state = SpeedLimitAssistState.inactive
    elif self.speed_limit_changed and self.apply_confirm_speed_threshold:
        self.state = SpeedLimitAssistState.preActive  # Only if apply_confirm_speed_threshold is True
```

```python
# Lines 193-201: apply_confirm_speed_threshold logic
@property
def apply_confirm_speed_threshold(self) -> bool:
    # below CST: always require user confirmation
    if self.v_cruise_cluster_below_confirm_speed_threshold:
        return True
    # at/above CST:
    # - new speed limit >= CST: auto change (returns False - no confirmation needed)
    # - new speed limit < CST: user confirmation required (returns True)
    return bool(self.speed_limit_final_last_conv < CONFIRM_SPEED_THRESHOLD[self.is_metric])
```

**The Bug**: The logic assumes that if both current and new speeds are above CST, no confirmation is needed. However, in ICBM mode, SLA still needs to transition to `preActive` to allow ICBM to adjust, even for increases above CST.

### Workaround

Currently, there is no workaround. If you're in a 50 mph zone and enter a 60 mph zone, you would need to:
1. Manually increase the cluster setpoint, OR
2. Wait for SLA to become inactive (e.g., by manually changing setpoint), then it will transition to preActive when speed limit changes

This is likely a bug that should be fixed to allow automatic speed increases above the CST threshold in ICBM mode.

## Why SLA May Not Adjust Cluster Setpoint

Based on the code analysis, here are the likely reasons why SLA only adjusts the cluster setpoint ~1/3 of the time:

### 1. SLA Not Active

**Condition**: `sla.is_active = False`

**Possible Causes:**
- SLA is in `preActive` state but timeout expires before cluster matches target
- SLA transitions to `inactive` due to manual cluster changes
- Speed limit not detected or invalid
- SLA not enabled in settings (`SpeedLimitMode != assist`)

**Debug Check**: Monitor `longitudinalPlanSP.speedLimit.assist.active` and `longitudinalPlanSP.speedLimit.assist.state`

### 2. SLA Not Providing Target

**Condition**: `sla.output_v_target = V_CRUISE_UNSET` (255.0)

**Code Logic** (`get_v_target_from_control()`):
```python
if self._has_speed_limit:
    if self.pcm_op_long and self.is_enabled:
        return self._speed_limit_final_last
    if not self.pcm_op_long and self.is_active:  # ICBM mode requires is_active
        return self._speed_limit_final_last

    # ICBM vehicles also get target in preActive state to break circular dependency
    if (not self.pcm_op_long and self.is_enabled and
        self.state == SpeedLimitAssistState.preActive and
        self.CP_SP.intelligentCruiseButtonManagementAvailable):
        return self._speed_limit_final_last
return V_CRUISE_UNSET
```

**Key Point**: In ICBM mode, SLA provides target when `is_active = True` OR when in `preActive` state with ICBM available (fix applied). Without the fix, SLA would only provide target when `is_active = True`, creating a circular dependency.

### 3. Another Source Has Lower Target

**Condition**: Another longitudinal plan source (cruise, SCC-Vision, SCC-Map) has a lower `v_target`

**Code Logic**:
```python
targets = {
    LongitudinalPlanSource.cruise: (v_cruise, a_ego),
    LongitudinalPlanSource.sccVision: (self.scc.vision.output_v_target, ...),
    LongitudinalPlanSource.sccMap: (self.scc.map.output_v_target, ...),
    LongitudinalPlanSource.speedLimitAssist: (self.sla.output_v_target, ...),
}
self.source = min(targets, key=lambda k: targets[k][0])
```

**Impact**: If SCC-Vision or SCC-Map provides a lower target (e.g., slowing for a curve), that becomes the active source, not SLA. ICBM will adjust to that target instead.

### 4. ICBM Not Ready

**Condition**: `icbm.is_ready = False`

**Possible Causes:**
- Longitudinal control not enabled
- Cruise control override active
- User manually pressing cruise buttons
- Cancel/resume commands active

**Debug Check**: Monitor ICBM readiness state

### 5. ICBM Rate Limiting

**Condition**: Button presses rate-limited to 0.05s minimum interval

**Impact**: Large speed limit changes may take multiple cycles to adjust. If speed limit changes frequently, ICBM may not keep up.

### 6. PreActive Timeout

**Condition**: SLA enters `preActive` state but `pre_active_timer` expires (5 seconds)

**Impact**: If cluster setpoint doesn't match target within 5 seconds, SLA transitions to `inactive` and stops providing target.

**Possible Causes**:
- ICBM not sending buttons (not ready, rate limited, etc.)
- Buttons not being processed by vehicle (CAN bus issues)
- Cluster setpoint not updating despite button presses

### 7. Manual Button Press Detection

**Condition**: User or system presses buttons, triggering `_get_button_release()` logic

**Impact**: If buttons are pressed within 0.5s window, SLA may think user is manually adjusting and delay activation.

## Expected Behavior Flow (ICBM + SLA)

### Scenario: Speed Limit Changes from 55 mph to 45 mph

1. **Initial State**:
   - Cluster setpoint: 55 mph
   - Speed limit: 55 mph
   - SLA state: `active`
   - ICBM state: `holding`

2. **Speed Limit Changes to 45 mph**:
   - `SpeedLimitResolver` detects new limit: 45 mph
   - SLA receives update: `speed_limit_final_last = 45 mph`
   - SLA calculates: `target_set_speed_conv = 45 mph`
   - SLA checks: `target_set_speed_confirmed = False` (55 ≠ 45)
   - SLA checks: `apply_confirm_speed_threshold` (45 < 50 mph CST = True)
   - SLA transitions: `active` → `preActive`
   - SLA sets: `pre_active_timer = 5 seconds`

3. **SLA PreActive State** (WITH FIX):
   - SLA provides target: `output_v_target = 45 mph` (because fix allows target in preActive state when ICBM available) ✅
   - ICBM receives target from `longitudinalPlanSP.vTarget = 45 mph`
   - ICBM compares: 45 < 55, needs to decrease
   - ICBM transitions: `holding` → `preActive` → `decreasing`
   - ICBM sends decrease button presses

4. **ICBM Adjusting**:
   - ICBM sends decrease buttons at 0.05s intervals
   - Cluster setpoint decreases: 55 → 54 → 53 → ... → 45 mph
   - When cluster reaches 45 mph, ICBM transitions to `holding`

5. **SLA Becomes Active**:
   - SLA checks: `target_set_speed_confirmed = True` (45 == 45)
   - SLA transitions: `preActive` → `active` ✅
   - System is now cruising at 45 mph

## Real-World Example: The Dependency Loop (Before Fix)

**Scenario**: Speed limit changes from 50 mph to 40 mph while SLA is active

**Timeline** (BEFORE FIX):

**At 36 seconds** (Initial activation):
- Button press (type 10 = resumeCruise) detected
- ICBM: `inactive (0)` → `preActive (1)` → `holding (4)` ✅
- SLA: `disabled (0)` → `active (5)` ✅
- SLA `vTarget`: `255` → `22.352 m/s` (50 mph) ✅
- `speedCluster`: `22.252 m/s` (50 mph) - matches target ✅
- Everything working correctly!

**At 149 seconds** (Speed limit changes to 40 mph):
- New speed limit detected: `17.88 m/s` (40 mph)
- Speed limit < CST (50 mph), so `apply_confirm_speed_threshold = True`
- SLA transitions: `active (5)` → `preActive (2)` (waiting for cluster to match)
- **Problem**: SLA `is_active` becomes `False` (only True in active/adapting states)
- SLA `vTarget`: `22.352` → `255` (V_CRUISE_UNSET) ❌ (BEFORE FIX)
- SLA `active`: `1` → `0` ❌
- ICBM state: stays at `4` (holding) - has no target to adjust to!
- `speedCluster`: stays at `22.352 m/s` (50 mph) - doesn't decrease!
- SLA can't become active because cluster (50) doesn't match target (40)
- After 5 seconds: `preActive` timer expires → SLA goes to `inactive`

**The Dependency Loop** (BEFORE FIX):
1. SLA needs to be `active` to provide `vTarget` (22.352 → 255 when becomes preActive)
2. SLA won't become `active` until cluster matches target (`target_set_speed_confirmed = True`)
3. ICBM needs `vTarget` to know what to adjust cluster to
4. ICBM can't adjust because SLA doesn't provide target
5. **Result**: Deadlock - SLA stuck in preActive, ICBM has nothing to do

**Root Cause** (BEFORE FIX):
```python
# Line 132-133 in speed_limit_assist.py (BEFORE FIX)
if not self.pcm_op_long and self.is_active:  # ICBM mode requires is_active
    return self._speed_limit_final_last
```

SLA only provided target when `is_active = True`, but `is_active` is only True in `active` or `adapting` states, not `preActive`. This created the circular dependency.

**The Fix**:
```python
# Lines 141-145 in speed_limit_assist.py (AFTER FIX)
# ICBM vehicles also get target in preActive state to break circular dependency
if (not self.pcm_op_long and self.is_enabled and
    self.state == SpeedLimitAssistState.preActive and
    self.CP_SP.intelligentCruiseButtonManagementAvailable):
    return self._speed_limit_final_last
```

Now SLA provides target in `preActive` state when ICBM is available, breaking the circular dependency.

## Potential Design Limitation

There is a design limitation where:

1. SLA in `preActive` state does not provide `output_v_target` (because `is_active = False`) - **FIXED**: Now provides target when ICBM available
2. ICBM needs `v_target` from `longitudinalPlanSP.vTarget` to know what to adjust cluster setpoint to
3. SLA won't transition to `active` until cluster matches target (`target_set_speed_confirmed = True`)
4. But cluster won't match target unless ICBM adjusts it
5. ICBM can't adjust it because SLA doesn't provide target (returns `V_CRUISE_UNSET = 255.0`) - **FIXED**: Now provides target in preActive

**How It Currently Works** (AFTER FIX):

The longitudinal planner selects the minimum target from all sources:
```python
targets = {
    LongitudinalPlanSource.cruise: (v_cruise, a_ego),
    LongitudinalPlanSource.sccVision: (self.scc.vision.output_v_target, ...),
    LongitudinalPlanSource.sccMap: (self.scc.map.output_v_target, ...),
    LongitudinalPlanSource.speedLimitAssist: (self.sla.output_v_target, ...),  # Now provides target in preActive
}
self.source = min(targets, key=lambda k: targets[k][0])
```

When SLA is in `preActive` state with ICBM available, it now provides the target, allowing ICBM to adjust the cluster setpoint.

## Debugging Recommendations

### Critical Values to Check When SLA Isn't Adjusting

When SLA is active (`assist.state = 5`, `assist.active = 1`) but ICBM isn't adjusting, check these values in order:

#### 1. **SLA Target Value** (Most Critical)
```
longitudinalPlanSP.speedLimit.assist.vTarget
```
- **Expected**: Should be ~22.35 m/s (50 mph) or ~24.58 m/s (55 mph) - the speed limit + offset in m/s
- **Problem**: If this is `255.0` (V_CRUISE_UNSET), SLA is not providing a target despite being active
- **Why**: In ICBM mode, SLA provides target when `is_active = True` OR when in `preActive` state with ICBM available (fix)

#### 2. **Longitudinal Plan Source** (Critical)
```
longitudinalPlanSP.longitudinalPlanSource
```
- **Expected**: Should be `3` (speedLimitAssist) when SLA should be controlling
- **Values**:
  - `0` = cruise (user setpoint)
  - `1` = sccVision (Smart Cruise Control - Vision)
  - `2` = sccMap (Smart Cruise Control - Map)
  - `3` = speedLimitAssist
- **Problem**: If this is NOT `3`, another source has a lower target and is overriding SLA
- **Example**: If SCC-Vision detects a curve ahead and wants to slow to 45 mph, it will override SLA's 50 mph target

#### 3. **Actual Target ICBM Receives**
```
longitudinalPlanSP.vTarget
```
- **Expected**: Should match `assist.vTarget` if SLA is the selected source
- **Problem**: If this differs from `assist.vTarget`, another source is active
- **Note**: This is what ICBM actually uses to determine button presses

#### 4. **Speed Limit Detection**
```
longitudinalPlanSP.speedLimit.resolver.speedLimitFinalLast
```
- **Expected**: Should be ~22.35 m/s (50 mph) - the detected speed limit + offset in m/s
- **Problem**: If this is `0` or wrong value, speed limit detection failed
- **Also check**:
  - `resolver.speedLimitValid` - Is current speed limit valid?
  - `resolver.speedLimitLastValid` - Was previous speed limit valid?
  - `resolver.source` - Where did speed limit come from? (`1` = car, `2` = map)

#### 5. **Current Cluster Setpoint**
```
carState.cruiseState.speedCluster
```
- **Expected**: Should be in m/s (e.g., 24.58 m/s = 55 mph)
- **Problem**: If this doesn't match the target, ICBM should be adjusting
- **Compare**: This vs. `longitudinalPlanSP.vTarget` - ICBM should adjust until they match

#### 6. **ICBM Readiness** (If Available in Logs)
ICBM state is not directly published, but you can infer readiness from:
- `carControl.enabled` - Must be `True` for ICBM to work
- `carControl.cruiseControl.override` - Must be `False`
- `carControl.cruiseControl.cancel` - Must be `False`
- `carControl.cruiseControl.resume` - Must be `False`
- Manual button presses - If user is pressing buttons, ICBM won't work

#### 7. **SLA State Details**
```
longitudinalPlanSP.speedLimit.assist.state
```
- **Your case**: `5` = `active` (correct)
- **Other states**:
  - `0` = disabled
  - `1` = inactive
  - `2` = preActive (waiting for cluster to match)
  - `3` = pending (awaiting speed limit)
  - `4` = adapting (reducing speed)

### Debugging Checklist for Your Specific Case

You're seeing:
- ✅ `assist.state = 5` (active) - SLA is active
- ✅ `assist.active = 1` - SLA is active
- ✅ `assist.enabled = 1` - SLA is enabled
- ❓ Cluster not adjusting

**Next steps - Check these values:**

1. **`assist.vTarget`** - Is it `255.0` or a real value (~22.35 m/s for 50 mph)?
   - If `255.0`: SLA bug - active but not providing target
   - If real value: Continue to step 2

2. **`longitudinalPlanSource`** - Is it `3` (speedLimitAssist)?
   - If NOT `3`: Another source is overriding (likely SCC-Vision/Map)
   - If `3`: Continue to step 3

3. **`vTarget`** - Does it match `assist.vTarget`?
   - If different: Source selection issue
   - If same: Continue to step 4

4. **`resolver.speedLimitFinalLast`** - Is it ~22.35 m/s (50 mph)?
   - If wrong: Speed limit detection issue
   - If correct: Continue to step 5

5. **`carState.cruiseState.speedCluster`** - What is current setpoint?
   - If it's ~24.58 m/s (55 mph): ICBM should be decreasing
   - If it matches target: No adjustment needed (but why did you see the message?)

6. **Check for button presses** - Were you or the system pressing cruise buttons?
   - ICBM won't work if buttons are being pressed

### Common Issues Based on Values

| `assist.vTarget` | `longitudinalPlanSource` | `vTarget` | Likely Issue |
|-----------------|-------------------------|-----------|--------------|
| 255.0 | Any | Any | SLA not providing target (bug) |
| Real value | 0, 1, or 2 | Different from assist.vTarget | Another source overriding SLA |
| Real value | 3 | Matches assist.vTarget | ICBM not ready or not working |
| 255.0 | 3 | Different value | SLA not selected, using cruise/SCC instead |

### Real-World Example Analysis

**Your Case**: Speed limit changed from 55 mph to 50 mph

**SLA Values** (All Correct ✅):
- `assist.vTarget`: Changed from 24.5872 to 22.352 m/s (55→50 mph) ✅
- `longitudinalPlanSource`: `3` (speedLimitAssist) ✅
- `vTarget`: Changed from 24.5872 to 22.352 m/s ✅
- `resolver.speedLimitFinalLast`: 22.352 m/s (50 mph) ✅
- `speedCluster`: 24.587 m/s (55 mph) - needs to decrease ✅

**Conclusion**: SLA is working perfectly. The problem is with ICBM not adjusting the cluster.

**Next Steps - Check ICBM Values**:

1. **`selfdriveStateSP.intelligentCruiseButtonManagement.state`**
   - **Expected**: Should be `3` (decreasing) when trying to reduce speed
   - **Values**: `0` = inactive, `1` = preActive, `2` = increasing, `3` = decreasing, `4` = holding
   - **If `0` (inactive)**: ICBM is not ready (check readiness conditions below)

2. **`selfdriveStateSP.intelligentCruiseButtonManagement.sendButton`**
   - **Expected**: Should be `2` (decrease) when sending decrease buttons
   - **Values**: `0` = none, `1` = increase, `2` = decrease
   - **If `0` (none)**: ICBM is not sending buttons

3. **`selfdriveStateSP.intelligentCruiseButtonManagement.vTarget`**
   - **Expected**: Should be `50` (mph) after rounding from 22.352 m/s
   - **Compare**: This vs. `speedCluster` (should be 55 mph) - ICBM should see 50 < 55

4. **ICBM Readiness** (Check `carControl` values):
   - `carControl.enabled` - Must be `True`
   - `carControl.cruiseControl.override` - Must be `False`
   - `carControl.cruiseControl.cancel` - Must be `False`
   - `carControl.cruiseControl.resume` - Must be `False`
   - **Button presses**: Check if any cruise buttons were pressed (would prevent ICBM)

**Most Likely Issues**:
1. **ICBM state = `0` (inactive)**: ICBM is not ready - check `carControl` values
2. **ICBM sendButton = `0` (none)**: ICBM is not sending buttons despite being in decreasing state
3. **ICBM stuck in preActive**: `pre_active_timer` hasn't expired yet (0.4s delay)
4. **CAN bus issue**: Buttons are being sent but vehicle isn't processing them

### ICBM Stuck in Inactive State

**Problem**: `selfdriveStateSP.intelligentCruiseButtonManagement.state = 0` (inactive) the entire drive

**Important Discovery**: ICBM was working previously (states 1 = preActive, 4 = holding), indicating this is a regression from a recent code change, not a fundamental design issue.

**Root Cause**: ICBM's `is_ready` is `False`, preventing it from transitioning to `preActive` state.

**ICBM Readiness Requirements** (`update_readiness()`):
```python
ready = CC.enabled and not CC.cruiseControl.override and not CC.cruiseControl.cancel and not CC.cruiseControl.resume
button_pressed = any(self.cruise_button_timers[k] > 0 for k in self.cruise_button_timers)
self.is_ready = ready and not button_pressed
```

**Check These Values**:

1. **`carControl.enabled`**
   - **Must be**: `True`
   - **If `False`**: Longitudinal control is not enabled, ICBM won't work

2. **`carControl.cruiseControl.override`**
   - **Must be**: `False`
   - **If `True`**: System is overriding cruise control, ICBM disabled

3. **`carControl.cruiseControl.cancel`**
   - **Must be**: `False`
   - **If `True`**: Cruise control is being cancelled, ICBM disabled

4. **`carControl.cruiseControl.resume`**
   - **Must be**: `False`
   - **If `True`**: Cruise control is being resumed, ICBM disabled

5. **Button Presses**
   - Check `carState.buttonEvents` for any cruise button presses
   - If buttons are being pressed (even briefly), ICBM won't work
   - Common culprits: accelCruise, decelCruise, setCruise, resumeCruise

6. **Early Exit Check** (Less Likely):
   - ICBM exits early if `CP_SP.pcmCruiseSpeed = True`
   - But if this were the case, ICBM state might not be published at all
   - Check `carParamsSP.pcmCruiseSpeed` - should be `False` for ICBM to work

**Most Common Causes**:
1. **`carControl.enabled = False`**: Longitudinal control not engaged
2. **Button presses detected**: Even brief button presses prevent ICBM
3. **`carControl.cruiseControl.override = True`**: System override active
4. **Ford-specific issue**: Check if Ford's ICBM implementation has additional requirements

### Button Timer Issue

**Your Situation**:
- All `carControl` values are correct ✅
- Button types 8 (`mainCruise`) and 9 (`setCruise`) were pressed ~30 seconds before speed limit change
- ICBM state remains `0` (inactive) the entire drive

**Analysis**:
Button types 8 and 9 ARE tracked in `CRUISE_BUTTON_TIMER`:
```python
CRUISE_BUTTON_TIMER = {
    ButtonType.decelCruise: 0,
    ButtonType.accelCruise: 0,
    ButtonType.setCruise: 0,      # Type 9
    ButtonType.resumeCruise: 0,
    ButtonType.cancel: 0,
    ButtonType.mainCruise: 0      # Type 8
}
```

**Button Timer Logic**:
```python
def update_manual_button_timers(CS: car.CarState, button_timers: dict):
    # Increment timer for buttons still pressed
    for k in button_timers:
        if button_timers[k] > 0:
            button_timers[k] += 1

    # Process button events
    for b in CS.buttonEvents:
        if b.type.raw in button_timers:
            button_timers[b.type.raw] = 1 if b.pressed else 0  # Set to 0 on release
```

**Expected Behavior**:
- When buttons are pressed: timer = 1
- While buttons are held: timer increments each frame
- When buttons are released: timer = 0
- After 30 seconds: timer should definitely be 0

**Possible Issues**:

1. **Button Release Event Not Detected**:
   - Check if button release events (`pressed = False`) were properly emitted
   - Ford's button parsing might not be detecting releases correctly
   - Check `carState.buttonEvents` around the time buttons were pressed - were release events present?

2. **Timer Not Being Updated**:
   - ICBM's `update_readiness()` calls `update_manual_button_timers()` every frame
   - But if button events aren't being processed, timers won't reset
   - Verify that `carState.buttonEvents` contains release events for types 8 and 9

3. **Ford-Specific Button Parsing Issue**:
   - Ford uses combo buttons that emit different event types based on cruise state
   - `mainCruise` (type 8) and `setCruise` (type 9) might have special handling
   - Check Ford's `carstate_ext.py` button parsing logic

4. **Timer Persistence Bug**:
   - If timer gets stuck > 0, ICBM will never become ready
   - This could be a bug in the timer logic or button event processing

**Debugging Steps**:

1. **Check Button Events Around Press Time**:
   - Look at `carState.buttonEvents` when buttons 8 and 9 were pressed
   - Verify that release events (`pressed = False`) were emitted
   - If no release events: This is the bug!

2. **Check Button Events at Speed Limit Change**:
   - Look at `carState.buttonEvents` at the time of speed limit change
   - Are there any button events? Even brief ones?
   - Check if timers might have been reset and then set again

3. **Check ICBM Timer State** (If Available):
   - ICBM's `cruise_button_timers` dict is not directly published
   - But you can infer: if `is_ready = False` and all carControl values are correct, timers must be > 0

4. **Workaround Test**:
   - Try manually pressing and releasing cruise buttons after engaging
   - See if this "resets" ICBM and allows it to become ready
   - This would confirm a timer persistence issue

**Most Likely Root Cause**:
Given that buttons were pressed 30 seconds before and ICBM is still inactive, the most likely issue is that **button release events are not being properly detected or processed by Ford's button parsing logic**. This would cause timers to remain > 0 indefinitely, preventing ICBM from ever becoming ready.

### Additional Diagnostic: Button Timer Persistence

**If Button Releases Are Detected**:
If you can see button release events in the logs (dots disappearing), but ICBM is still inactive, check:

1. **Button Timer Increment Logic**:
   ```python
   # In update_manual_button_timers():
   for k in button_timers:
       if button_timers[k] > 0:
           button_timers[k] += 1  # Increments every frame while > 0
   ```
   - If a timer gets stuck > 0, it will increment indefinitely
   - Even if release events are detected, if the timer was already > 0 and the release event isn't processed correctly, it stays > 0

2. **Check for Multiple Button Events**:
   - Look for button events around the time buttons were pressed
   - Are there multiple press/release cycles?
   - Could a later button press have reset the timer after release?

3. **Check Timing of Release Events**:
   - When exactly did release events occur relative to when cruise was engaged?
   - If releases happened BEFORE `carControl.enabled = True`, ICBM might not have been checking yet
   - ICBM only runs when `carControl.enabled = True`

4. **Check if ICBM Was Running When Buttons Were Pressed**:
   - If buttons were pressed to ENGAGE cruise (types 8 and 9), `carControl.enabled` might have been `False` at that time
   - ICBM's `run()` method is called every frame, but if `enabled` was False, readiness wasn't being checked
   - When `enabled` became True, button timers might have already been set

5. **Potential Bug: Timer Not Reset on ICBM Start**:
   - If ICBM starts running AFTER buttons were pressed and released, the timers might have stale values
   - Check if timers are initialized/reset when ICBM first starts running

**Debugging Steps**:
1. Check the exact timing: When did `carControl.enabled` become `True` relative to button presses?
2. Check if there were any button events AFTER cruise was engaged
3. Try manually pressing and releasing cruise buttons AFTER cruise is engaged to see if this "resets" ICBM
4. Check if there's a way to see the actual timer values (they're not published, but you could add logging)

**Workaround Test**:
After engaging cruise control, try manually pressing and releasing the cruise buttons (increase/decrease). This should reset the timers and might allow ICBM to become ready. If this works, it confirms a timer persistence issue.

### Regression Analysis - What Could Break ICBM Readiness?

Since ICBM was working before (states 1 and 4), recent changes likely broke the readiness check. Look for changes that affect:

1. **Button Event Processing**:
   - Changes to `carState.buttonEvents` parsing
   - Changes to Ford's `carstate_ext.py` button detection logic
   - Changes to how button types 8 and 9 are handled
   - Changes to button release event detection

2. **CarControl Values**:
   - Changes to `carControl.enabled` logic
   - Changes to `carControl.cruiseControl.override` behavior
   - Changes to `carControl.cruiseControl.cancel` or `resume` logic

3. **ICBM Initialization**:
   - Changes to when ICBM's `run()` method is called
   - Changes to ICBM initialization timing
   - Changes to `CP_SP.pcmCruiseSpeed` logic

4. **Button Timer Logic**:
   - Changes to `update_manual_button_timers()` function
   - Changes to `CRUISE_BUTTON_TIMER` dictionary
   - Changes to how timers are reset

5. **Ford-Specific Changes**:
   - Changes to Ford's CAN message parsing
   - Changes to button signal detection
   - Changes to cruise control state detection

**Key Files to Review**:
- `opendbc_repo/opendbc/sunnypilot/car/ford/carstate_ext.py` - Button event parsing
- `sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py` - ICBM readiness logic
- `sunnypilot/selfdrive/car/cruise_ext.py` - Button timer logic
- `opendbc_repo/opendbc/car/ford/interface.py` - Ford interface initialization

**Debugging Steps**:
1. Check all `carControl` values listed above at the time of speed limit change
2. Check `carState.buttonEvents` for any cruise button activity
3. Verify ICBM is enabled in settings: `params.get("IntelligentCruiseButtonManagement")` should be `True`
4. Check if Ford has any special requirements (e.g., specific cruise control state)

### Additional Debugging

1. **Monitor SLA State**:
   - Log `longitudinalPlanSP.speedLimit.assist.state`
   - Track state transitions and timing

2. **Monitor Cluster Setpoint**:
   - Track `carState.cruiseState.speedCluster` changes
   - Compare with `vTarget` from longitudinal plan
   - Verify if button presses are actually changing cluster setpoint

3. **Check Timing**:
   - Monitor when speed limit changes vs. when SLA state changes
   - Check if there's a delay between detection and action

4. **Verify Speed Limit Detection**:
   - Log `speedLimitFinalLast` from resolver
   - Verify speed limits are being detected correctly
   - Check speed limit source (map vs. car camera)

## Code References

Key files for understanding SLA + ICBM integration:

- `sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_assist.py` - SLA implementation
- `sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py` - ICBM controller
- `sunnypilot/selfdrive/controls/lib/longitudinal_planner.py` - Longitudinal planner that coordinates sources
- `opendbc_repo/opendbc/sunnypilot/car/ford/icbm.py` - Ford-specific ICBM button interface
- `opendbc_repo/opendbc/sunnypilot/car/ford/carstate_ext.py` - Ford button event tracking
- `opendbc_repo/opendbc/car/ford/carstate.py` - CarState integration
- `opendbc_repo/opendbc/car/ford/interface.py` - ICBM availability flag

## Summary

**Important**: SLA with ICBM is **NOT** designed to automatically adjust the cluster cruise setpoint without user confirmation. SLA requires the user to press the up (+) or down (-) button to accept speed limit changes. This is by design to prevent unexpected speed adjustments.

The system works as follows:
1. **ICBM**: Automatically adjusts cluster setpoint based on SCC-V/M targets (curves) - ✅ No user confirmation needed
2. **SLA**: Prompts user to accept speed limit changes via button press - ✅ Requires user confirmation (by design)
3. **ICBM**: Adjusts cluster setpoint after user accepts SLA change - ✅ Works after confirmation

The interaction between SLA's state machine and ICBM's button management creates a complex dependency chain, but the fixes ensure ICBM can see SLA's target and adjust accordingly once the user confirms the change.

### Key Differences: OP Long vs ICBM

| Aspect | OP Long Mode | ICBM Mode |
|--------|-------------|-----------|
| **Target Setpoint** | Fixed at 70/80 mph | Actual speed limit (e.g., 45 mph) |
| **User Action Required** | Must manually set to 70/80 mph | Automatic via ICBM button presses |
| **Cluster Display** | Shows 70/80 mph (doesn't change) | Shows actual speed limit (changes) |
| **Speed Control** | OP Long controls gas/brake | OEM ACC controls gas/brake |
| **SLA State Machine** | `update_state_machine_pcm_op_long()` | `update_state_machine_non_pcm_long()` |

### The Core Challenge

The ~1/3 success rate (before fix) stemmed from a coordination issue:

1. **SLA must be `active` to provide target**: `get_v_target_from_control()` only returned speed limit when `is_active = True` (BEFORE FIX)
2. **SLA won't become `active` until cluster matches**: Requires `target_set_speed_confirmed = True`
3. **ICBM needs target to adjust cluster**: Reads `longitudinalPlanSP.vTarget`
4. **Circular dependency**: ICBM can't adjust without target, but SLA won't provide target until adjusted

**The Fix**: Allow SLA to provide target in `preActive` state when ICBM is available, breaking the circular dependency.

### When It Works

The system succeeds when:
- Speed limit changes are gradual (allows time for state transitions)
- ICBM is ready (`is_ready = True`) and responsive
- No other longitudinal sources (SCC-Vision/Map) provide lower targets
- Cluster setpoint updates reliably from button presses
- SLA transitions quickly through `preActive` → `active` states (now possible with fix)

### When It Fails

Common failure scenarios:
- SLA stuck in `preActive` state (timeout expires before cluster matches) - **Reduced with fix**
- Another source (SCC) provides lower target, so SLA target is ignored
- ICBM not ready (user pressing buttons, override active, etc.)
- Button presses not reaching vehicle (CAN bus issues)
- Cluster setpoint not updating despite button presses
- Speed limit changes too rapidly for state machine to keep up

### Recommended Next Steps

1. **Add comprehensive logging** to track:
   - SLA state transitions and timing
   - ICBM state and readiness
   - Cluster setpoint changes vs. button presses
   - Longitudinal plan source selection
   - Speed limit detection and changes

2. **Investigate potential fixes**:
   - ✅ Allow SLA to provide target in `preActive` state when ICBM is active (DONE)
   - Add ICBM fallback to use resolver's speed limit directly (if needed)
   - Modify state machine to be more responsive to ICBM adjustments (if needed)

3. **Test specific scenarios**:
   - Speed limit decreases (55 → 45 mph)
   - Speed limit increases (45 → 55 mph)
   - Rapid speed limit changes
   - Speed limits below Confirm Speed Threshold (50 mph)
   - Speed limits above Confirm Speed Threshold

This documentation should help identify the root cause of any intermittent behavior and guide debugging efforts.
