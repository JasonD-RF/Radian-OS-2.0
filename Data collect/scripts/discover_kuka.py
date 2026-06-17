"""
Browse the KUKA OPC UA address space and report every readable variable.

Run:
    python scripts/discover_kuka.py --config config/collectors.local.yaml --cell chesty
    python scripts/discover_kuka.py --config config/collectors.local.yaml --cell mattis

Output: scripts/discovered_<cell>_kuka.json  (full results)
        scripts/discovered_<cell>_kuka.txt   (human-readable summary)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from asyncua import Client, Node, ua

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.collectors.opc_collector import _resolve_security_string


# ---------------------------------------------------------------------------
# Node browsing
# ---------------------------------------------------------------------------

async def _read_safe(node: Node) -> tuple[Any, str | None]:
    """Return (value, error_string). Never raises."""
    try:
        return await node.read_value(), None
    except Exception as exc:
        return None, str(exc)[:120]


async def browse(
    node: Node,
    depth: int,
    max_depth: int,
    results: list[dict],
    path: str = "",
    visited: set | None = None,
) -> None:
    if visited is None:
        visited = set()
    node_id_str = str(node.nodeid)
    if node_id_str in visited or depth > max_depth:
        return
    visited.add(node_id_str)

    try:
        children = await node.get_children()
    except Exception:
        return

    for child in children:
        try:
            name_obj = await child.read_display_name()
            name = name_obj.Text or str(child.nodeid)
            nclass = await child.read_node_class()
            child_path = f"{path}/{name}" if path else name
            cid = str(child.nodeid)

            if nclass == ua.NodeClass.Variable:
                val, err = await _read_safe(child)
                try:
                    dtype = (await child.read_data_type_as_variant_type()).name
                except Exception:
                    dtype = "?"
                results.append({
                    "path": child_path,
                    "node_id": cid,
                    "dtype": dtype,
                    "value": repr(val) if val is not None else None,
                    "error": err,
                })
            elif nclass == ua.NodeClass.Object:
                results.append({
                    "path": child_path,
                    "node_id": cid,
                    "dtype": "Object",
                    "value": None,
                    "error": None,
                })
                await browse(child, depth + 1, max_depth, results, child_path, visited)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Targeted probes — KUKA DeviceConnect_Advanced known paths
# ---------------------------------------------------------------------------

# These are tested against real KUKA KRC5/KRC4 controllers with DeviceConnect_Advanced.
# Node IDs use the standard KUKA OPC UA numbering.
TARGETED_NODES: dict[str, str] = {
    # ---- Axis drive data (per axis) ----------------------------------------
    "axis_torque_a1":         "ns=9;s=5001.Robot.Axes.A1.ParameterSet.ActualTorque",
    "axis_torque_a2":         "ns=9;s=5001.Robot.Axes.A2.ParameterSet.ActualTorque",
    "axis_torque_a3":         "ns=9;s=5001.Robot.Axes.A3.ParameterSet.ActualTorque",
    "axis_torque_a4":         "ns=9;s=5001.Robot.Axes.A4.ParameterSet.ActualTorque",
    "axis_torque_a5":         "ns=9;s=5001.Robot.Axes.A5.ParameterSet.ActualTorque",
    "axis_torque_a6":         "ns=9;s=5001.Robot.Axes.A6.ParameterSet.ActualTorque",
    "axis_velocity_a1":       "ns=9;s=5001.Robot.Axes.A1.ParameterSet.ActualVelocity",
    "axis_velocity_a2":       "ns=9;s=5001.Robot.Axes.A2.ParameterSet.ActualVelocity",
    "axis_velocity_a3":       "ns=9;s=5001.Robot.Axes.A3.ParameterSet.ActualVelocity",
    "axis_velocity_a4":       "ns=9;s=5001.Robot.Axes.A4.ParameterSet.ActualVelocity",
    "axis_velocity_a5":       "ns=9;s=5001.Robot.Axes.A5.ParameterSet.ActualVelocity",
    "axis_velocity_a6":       "ns=9;s=5001.Robot.Axes.A6.ParameterSet.ActualVelocity",
    "drive_temp_a1":          "ns=9;s=5001.Robot.Axes.A1.ParameterSet.DriveTemperature",
    "drive_temp_a2":          "ns=9;s=5001.Robot.Axes.A2.ParameterSet.DriveTemperature",
    "drive_temp_a3":          "ns=9;s=5001.Robot.Axes.A3.ParameterSet.DriveTemperature",
    "drive_temp_a4":          "ns=9;s=5001.Robot.Axes.A4.ParameterSet.DriveTemperature",
    "drive_temp_a5":          "ns=9;s=5001.Robot.Axes.A5.ParameterSet.DriveTemperature",
    "drive_temp_a6":          "ns=9;s=5001.Robot.Axes.A6.ParameterSet.DriveTemperature",
    "motor_current_a1":       "ns=9;s=5001.Robot.Axes.A1.ParameterSet.MotorCurrent",
    "motor_current_a2":       "ns=9;s=5001.Robot.Axes.A2.ParameterSet.MotorCurrent",
    "motor_current_a3":       "ns=9;s=5001.Robot.Axes.A3.ParameterSet.MotorCurrent",
    "motor_current_a4":       "ns=9;s=5001.Robot.Axes.A4.ParameterSet.MotorCurrent",
    "motor_current_a5":       "ns=9;s=5001.Robot.Axes.A5.ParameterSet.MotorCurrent",
    "motor_current_a6":       "ns=9;s=5001.Robot.Axes.A6.ParameterSet.MotorCurrent",

    # ---- Robot-level motion / kinematics ------------------------------------
    "cart_velocity":          "ns=9;s=5001.Robot.ParameterSet.ActualCartesianVelocity",
    "path_velocity":          "ns=9;s=5001.Robot.ParameterSet.ActualPathVelocity",
    "power_on_duration":      "ns=9;s=5001.Robot.ParameterSet.PowerOnDuration",
    "motion_duration":        "ns=9;s=5001.Robot.ParameterSet.MotionDuration",
    "remaining_path":         "ns=9;s=5001.Robot.ParameterSet.RemainingPathLength",
    "flange_load":            "ns=9;s=5001.Robot.ParameterSet.FlangeLoad",

    # ---- Safety / workspace ------------------------------------------------
    "safe_op_status":         "ns=9;s=5005.SafetyState_1.ParameterSet.SafeOperationStatus",
    "velocity_monitoring":    "ns=9;s=5005.SafetyState_1.ParameterSet.VelocityMonitoringActive",
    "robot_workspace":        "ns=9;s=5005.SafetyState_1.ParameterSet.CurrentRobotWorkspace",
    "safe_range_monitoring":  "ns=9;s=5005.SafetyState_1.ParameterSet.SafeRangeMonitoringActive",

    # ---- Diagnostics / errors ----------------------------------------------
    "active_error_code":      "ns=9;s=5006.Diagnosis_1.ParameterSet.ActiveErrorMessageCode",
    "active_error_text":      "ns=9;s=5006.Diagnosis_1.ParameterSet.ActiveErrorMessageText",
    "active_warning_code":    "ns=9;s=5006.Diagnosis_1.ParameterSet.ActiveWarningMessageCode",
    "active_warning_text":    "ns=9;s=5006.Diagnosis_1.ParameterSet.ActiveWarningMessageText",
    "error_count":            "ns=9;s=5006.Diagnosis_1.ParameterSet.ErrorCount",

    # ---- Controller info ---------------------------------------------------
    "controller_name":        "ns=9;s=5100.ControllerInfo.ParameterSet.ControllerName",
    "serial_number":          "ns=9;s=5100.ControllerInfo.ParameterSet.SerialNumber",
    "robot_type":             "ns=9;s=5100.ControllerInfo.ParameterSet.RobotType",
    "sw_version":             "ns=9;s=5100.ControllerInfo.ParameterSet.SoftwareVersion",
    "kss_version":            "ns=9;s=5104.Robot.ParameterSet.KSSVersion",

    # ---- Program / interpreter ---------------------------------------------
    "sub_program_name":       "ns=9;s=5104.Robot.ParameterSet.SubProgramName",
    "interpreter_state":      "ns=9;s=5104.Robot.ParameterSet.InterpreterState",
    "bco_mode":               "ns=9;s=5104.Robot.ParameterSet.BCOMode",
    "current_line_comment":   "ns=9;s=5104.Robot.ParameterSet.CurrentLineComment",
    "advance_run":            "ns=9;s=5104.Robot.ParameterSet.AdvanceRun",

    # ---- I/O system --------------------------------------------------------
    "digital_in_1_32":        "ns=9;s=5002.IO_1.Inputs.DigitalInputs",
    "digital_out_1_32":       "ns=9;s=5002.IO_1.Outputs.DigitalOutputs",
    "analog_in_1":            "ns=9;s=5003.Analog_1.Inputs.AnalogInput1",
    "analog_in_2":            "ns=9;s=5003.Analog_1.Inputs.AnalogInput2",
    "analog_out_1":           "ns=9;s=5003.Analog_1.Outputs.AnalogOutput1",
    "analog_out_2":           "ns=9;s=5003.Analog_1.Outputs.AnalogOutput2",

    # ---- Energy / power ----------------------------------------------------
    "dc_bus_voltage":         "ns=9;s=5001.Robot.ParameterSet.DCBusVoltage",
    "total_power_w":          "ns=9;s=5001.Robot.ParameterSet.TotalPower",
    "energy_consumed_kwh":    "ns=9;s=5001.Robot.ParameterSet.EnergyConsumed",

    # ---- KRL variable bridge — additional ArcTech / SOP vars ---------------
    "torque_axis":            "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$TORQUE_AXIS",
    "vel_cp":                 "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$VEL.CP",
    "vel_axis":               "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$VEL_AXIS_MA",
    "acc_cp":                 "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$ACC.CP",
    "load_mass":              "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$LOAD.M",
    "load_com_x":             "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$LOAD.CM.X",
    "load_com_y":             "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$LOAD.CM.Y",
    "load_com_z":             "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$LOAD.CM.Z",
    "tool_data":              "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$TOOL",
    "base_data":              "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$BASE",
    "flange_tool":            "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$NULLFRAME",
    # ArcTech print state (SOP variables not yet in collector)
    "g_next_layer":           "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gNextLayer",
    "g_next_seam":            "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gNextSeam",
    "g_total_seam":           "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gActiveTotalSeam",
    "g_layer_count":          "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gLayerCount",
    "g_layer_seam_count":     "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gLayerSeamCount",
    "g_total_seam_count":     "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gTotalSeamCount",
    "g_print_active":         "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gPrintActive",
    "g_print_resume":         "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gPrintResume",
    "g_new_print":            "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gNewPrint",
    "g_print_complete":       "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gPrintComplete",
    "g_stop_cycle":           "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gStopCycle",
    "g_layer_rerun":          "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gLayerRerun",
    "g_skip_layer":           "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gSkipLayer",
    "g_first_seam_rerun":     "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gFirstSeamRerun",
    "g_last_seam_rerun":      "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gLastSeamRerun",
    "g_interpass_cleaning":   "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gInterpassCleaning",
    "g_last_error":           "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gLastError",
    "g_seams_in_layer":       "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#cSeamsInLayer",
    "g_outer_wall_layer":     "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gDoOuterWallLayer",
    "g_brim_layer":           "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gDoBrimLayer",
    "g_infill_layer":         "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gDoInfillLayer",
    "g_roof_floor_layer":     "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gDoRoofFloorLayer",
    "g_pyro_raw":             "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gPyroTempRawC",
    "g_pyro_target":          "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gPyroTargetTempC",
    "g_pyro_ok":              "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gPyroTempOK",
    "g_interpass_wait":       "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gInterpassWaitActive",
    "g_wire_feed_override":   "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gWireFeedOverride",
    "g_travel_speed_ovr":     "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1/Global#gTravelSpeedOverride",
    "act_line":               "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$ACT_LINE",
    "peri_rdy":               "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$PERI_RDY",
    "drives_off":             "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$DRIVES_OFF",
    "user_ov":                "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$OV_PRO",
    "e_stopped":              "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$PRO_STATE",
    "rob_stopped":            "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$ROB_STOP",
    "ipoc":                   "ns=9;s=ns=8%3Bi=5004??krlvar:///System/R1#$IPOC",
}


async def probe_targeted(client: Client, base_dir: Path) -> dict[str, dict]:
    """Try to read each targeted node, return results keyed by name."""
    results = {}
    nodes = []
    keys = list(TARGETED_NODES.keys())

    for key in keys:
        node_id = TARGETED_NODES[key]
        try:
            nodes.append(client.get_node(node_id))
        except Exception:
            nodes.append(None)

    for key, node in zip(keys, nodes):
        if node is None:
            results[key] = {"node_id": TARGETED_NODES[key], "value": None, "error": "node creation failed"}
            continue
        val, err = await _read_safe(node)
        results[key] = {
            "node_id": TARGETED_NODES[key],
            "value": repr(val) if val is not None else None,
            "error": err,
        }

    return results


async def get_namespaces(client: Client) -> list[str]:
    ns_array = await client.get_namespace_array()
    return list(ns_array)


async def run(cell: str, cfg: dict, base_dir: Path, max_browse_depth: int) -> None:
    robot_cfg = next(r for r in cfg["robots"] if r["id"] == cell)
    kuka = robot_cfg["kuka"]
    url = kuka["url"]
    security = kuka.get("security_string")
    username = kuka.get("username")
    password = kuka.get("password")

    print(f"\nConnecting to {cell} KUKA at {url} ...")
    client = Client(url=url, timeout=15.0)
    if username:
        client.set_user(username)
    if password:
        client.set_password(password)
    if security:
        await client.set_security_string(_resolve_security_string(base_dir, security, cell))

    async with client:
        print("Connected.\n")

        # Namespace list
        ns_list = await get_namespaces(client)
        print("=== Namespaces ===")
        for i, ns in enumerate(ns_list):
            print(f"  [{i}] {ns}")
        print()

        # Targeted probe
        print(f"=== Targeted probe ({len(TARGETED_NODES)} nodes) ===")
        targeted = await probe_targeted(client, base_dir)

        ok_nodes = {}
        failed_nodes = {}
        for name, result in targeted.items():
            if result["error"] is None:
                ok_nodes[name] = result
            else:
                failed_nodes[name] = result

        print(f"\nReadable ({len(ok_nodes)}):")
        for name, r in ok_nodes.items():
            print(f"  {name:35s}  {r['value'][:80] if r['value'] else 'None'}")

        print(f"\nNot accessible ({len(failed_nodes)}):")
        for name, r in failed_nodes.items():
            short_err = (r['error'] or '')[:60]
            print(f"  {name:35s}  {short_err}")

        # Address space browse
        browse_results: list[dict] = []
        if max_browse_depth > 0:
            print(f"\n=== Browsing address space (depth={max_browse_depth}) ===")
            objects = client.get_node("ns=0;i=85")
            await browse(objects, 0, max_browse_depth, browse_results)
            var_nodes = [r for r in browse_results if r["dtype"] != "Object"]
            print(f"Found {len(browse_results)} nodes total, {len(var_nodes)} variables")

        # Write output files
        out_dir = Path(__file__).parent
        json_out = out_dir / f"discovered_{cell}_kuka.json"
        txt_out  = out_dir / f"discovered_{cell}_kuka.txt"

        report = {
            "cell": cell,
            "url": url,
            "namespaces": ns_list,
            "targeted_readable": ok_nodes,
            "targeted_failed": failed_nodes,
            "browse_results": browse_results,
        }
        json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        with txt_out.open("w", encoding="utf-8") as f:
            f.write(f"KUKA OPC UA Discovery — {cell} ({url})\n")
            f.write("=" * 70 + "\n\n")
            f.write("NAMESPACES\n")
            for i, ns in enumerate(ns_list):
                f.write(f"  [{i}] {ns}\n")
            f.write("\nREADABLE NODES\n")
            for name, r in ok_nodes.items():
                f.write(f"  {name:35s}  node_id={r['node_id']}\n")
                f.write(f"  {'':35s}  value={r['value']}\n\n")
            f.write("\nFAILED NODES\n")
            for name, r in failed_nodes.items():
                f.write(f"  {name:35s}  {r['error']}\n")
            if browse_results:
                f.write("\nFULL ADDRESS SPACE BROWSE\n")
                for r in browse_results:
                    indent = "  " * r["depth"]
                    val = f" = {r['value'][:60]}" if r["value"] else ""
                    f.write(f"  {indent}{r['path']}  [{r['node_id']}]{val}\n")

        print(f"\nResults saved to:\n  {json_out}\n  {txt_out}\n")


def main() -> None:
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/collectors.local.yaml")
    parser.add_argument("--cell", default="chesty", choices=["chesty", "mattis"])
    parser.add_argument("--browse-depth", type=int, default=0,
                        help="Depth to browse full address space (0=skip, 3=thorough). "
                             "Depth 3 can take 2-5 minutes.")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    cfg = yaml.safe_load(cfg_path.read_text())
    base_dir = cfg_path.resolve().parent

    asyncio.run(run(args.cell, cfg, base_dir, args.browse_depth))


if __name__ == "__main__":
    main()
