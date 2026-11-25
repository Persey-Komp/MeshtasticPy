#!/usr/bin/env python3
"""
meshtastic_connect.py

Graphical Meshtastic chat client for a device connected over USB.

Features:
    - Connect / disconnect over USB serial (auto-detect or explicit port)
    - List the other nodes on the mesh
    - Send broadcast messages or direct messages to a selected node
    - Receive and display incoming text messages

Usage:
    python3 meshtastic_connect.py                      # open the GUI
    python3 meshtastic_connect.py --port /dev/ttyUSB0  # pre-fill the port
    python3 meshtastic_connect.py --cli                # terminal-only mode
    python3 meshtastic_connect.py --cli --send "hi"    # send, then exit
"""

import argparse
import queue
import sys
import threading
import time

BROADCAST = "^all"

try:
    from meshtastic.serial_interface import SerialInterface
    from pubsub import pub
except ImportError:
    print(
        "Missing dependencies. Install them with:\n"
        "    pip install meshtastic\n"
        "and make sure your user can access serial ports (see README.md).",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    TK_OK = True
except ImportError:
    tk = None
    TK_OK = False


# ---------------------------------------------------------------------------
# Terminal-only mode (the original behaviour, kept as --cli)
# ---------------------------------------------------------------------------

def run_cli(port, send_text):
    print("Connecting to Meshtastic device...")
    try:
        interface = SerialInterface(devPath=port)
    except Exception as exc:
        sys.exit(
            f"Failed to connect: {exc}\n"
            "Check the cable, the port name, and your serial permissions (see README.md)."
        )

    print("Connected!")
    print(f"  My node number: {interface.myInfo.my_node_num}")
    print(f"  Known nodes on the mesh: {len(interface.nodes)}")
    for node_id, node in interface.nodes.items():
        user = node.get("user", {})
        print(f"    - {user.get('longName', 'unknown')} ({node_id})")

    if send_text:
        print(f"Sending: {send_text}")
        interface.sendText(send_text)
        time.sleep(2)  # give the radio a moment to transmit
        interface.close()
        return

    def on_receive(packet, interface):
        decoded = packet.get("decoded", {})
        text = decoded.get("text")
        from_id = packet.get("fromId", "unknown")
        port_name = decoded.get("portnum", "RAW")
        stamp = time.strftime("%H:%M:%S")
        if text:
            print(f"[{stamp}] {from_id} ({port_name}): {text}")
        else:
            print(f"[{stamp}] {from_id} ({port_name}): {packet}")

    pub.subscribe(on_receive, "meshtastic.receive")
    print("Listening for packets... press Ctrl+C to quit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nClosing connection.")
    finally:
        interface.close()


# ---------------------------------------------------------------------------
# GUI mode
# ---------------------------------------------------------------------------

class MeshtasticGUI:
    POLL_MS = 100           # how often we check for events from the radio thread
    NODE_REFRESH_MS = 5000  # how often we rebuild the node list

    def __init__(self, root, initial_port=None):
        self.root = root
        self.root.title("Meshtastic Chat")
        self.root.geometry("860x560")
        self.root.minsize(680, 440)

        self.interface = None
        self.connected = False
        self.dest_id = BROADCAST
        self.events = queue.Queue()  # thread-safe bridge to the Tk main loop

        self._build_ui(initial_port)

        # Radio callbacks fire on the meshtastic reader thread; they only put
        # events on the queue, and _poll_events() applies them on the Tk main
        # thread, so Tk is never touched from a background thread.
        pub.subscribe(self._on_packet, "meshtastic.receive")
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")

        self._log("Enter the serial port (empty = auto-detect) and click Connect.")
        self.root.after(self.POLL_MS, self._poll_events)
        self.root.after(self.NODE_REFRESH_MS, self._refresh_nodes)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI construction ----------------------------------------------------

    def _build_ui(self, initial_port):
        top = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top.pack(fill=tk.X)

        ttk.Label(top, text="Port (empty = auto):").pack(side=tk.LEFT)
        self.port_entry = ttk.Entry(top, width=18)
        self.port_entry.pack(side=tk.LEFT, padx=(4, 8))
        if initial_port:
            self.port_entry.insert(0, initial_port)

        self.connect_btn = ttk.Button(top, text="Connect", command=self._connect)
        self.connect_btn.pack(side=tk.LEFT)
        self.disconnect_btn = ttk.Button(
            top, text="Disconnect", command=self._disconnect, state=tk.DISABLED
        )
        self.disconnect_btn.pack(side=tk.LEFT, padx=(6, 12))

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.LEFT)

        main = ttk.Frame(self.root, padding=(8, 4))
        main.pack(fill=tk.BOTH, expand=True)

        nodes_frame = ttk.LabelFrame(main, text="Nodes on the mesh", padding=4)
        nodes_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

        self.node_tree = ttk.Treeview(
            nodes_frame, columns=("name", "id"), show="headings", height=18
        )
        self.node_tree.heading("name", text="Name")
        self.node_tree.heading("id", text="ID")
        self.node_tree.column("name", width=150)
        self.node_tree.column("id", width=110)
        self.node_tree.pack(fill=tk.BOTH, expand=True)
        self.node_tree.bind("<<TreeviewSelect>>", self._on_node_select)

        self.broadcast_btn = ttk.Button(
            nodes_frame, text="Message everyone", command=self._select_broadcast
        )
        self.broadcast_btn.pack(fill=tk.X, pady=(6, 0))

        chat_frame = ttk.LabelFrame(main, text="Chat", padding=4)
        chat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.dest_var = tk.StringVar(value="To: everyone (broadcast)")
        ttk.Label(chat_frame, textvariable=self.dest_var, anchor="w").pack(
            fill=tk.X, pady=(0, 4)
        )

        self.chat = scrolledtext.ScrolledText(
            chat_frame, state=tk.DISABLED, wrap=tk.WORD
        )
        self.chat.pack(fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        bottom.pack(fill=tk.X)

        self.msg_entry = ttk.Entry(bottom)
        self.msg_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.msg_entry.bind("<Return>", lambda _event: self._send())
        self.send_btn = ttk.Button(
            bottom, text="Send", command=self._send, state=tk.DISABLED
        )
        self.send_btn.pack(side=tk.LEFT, padx=(6, 0))

    # ---- connection handling --------------------------------------------------

    def _connect(self):
        if self.connected:
            return
        port = self.port_entry.get().strip() or None
        self._set_status(f"Connecting to {port or 'auto-detected device'}...")
        self.connect_btn.config(state=tk.DISABLED)
        threading.Thread(
            target=self._connect_worker, args=(port,), daemon=True
        ).start()

    def _connect_worker(self, port):
        # Runs on a worker thread so the GUI stays responsive; SerialInterface
        # can take a couple of seconds to negotiate with the device.
        try:
            interface = SerialInterface(devPath=port)
            self.events.put(("connected", interface))
        except Exception as exc:
            self.events.put(("connect_error", str(exc)))

    def _on_connected(self, interface):
        self.interface = interface
        self.connected = True
        my_num = getattr(interface.myInfo, "my_node_num", "?")
        self._set_status(f"Connected (my node number: {my_num})")
        self.port_entry.config(state=tk.DISABLED)
        self.disconnect_btn.config(state=tk.NORMAL)
        self.send_btn.config(state=tk.NORMAL)
        self._log("Connected to Meshtastic device.")
        self._refresh_nodes_now()

    def _on_connect_error(self, message):
        self._set_status("Disconnected")
        self.connect_btn.config(state=tk.NORMAL)
        messagebox.showerror(
            "Connection failed",
            f"{message}\n\n"
            "Check the cable, the port name, and your serial permissions "
            "(see README.md).",
        )

    def _disconnect(self):
        if self.interface:
            try:
                self.interface.close()
            except Exception:
                pass
        self.interface = None
        self.connected = False
        self._set_status("Disconnected")
        self.port_entry.config(state=tk.NORMAL)
        self.connect_btn.config(state=tk.NORMAL)
        self.disconnect_btn.config(state=tk.DISABLED)
        self.send_btn.config(state=tk.DISABLED)

    def _on_connection_lost(self, interface=None):
        self.events.put(("lost", None))

    # ---- radio callbacks (reader thread) ----------------------------------------

    def _on_packet(self, packet, interface):
        decoded = packet.get("decoded", {})
        text = decoded.get("text")
        if not text:
            return  # only chat messages are shown in the GUI
        self.events.put(
            (
                "packet",
                packet.get("fromId", "unknown"),
                decoded.get("portnum", "RAW"),
                text,
            )
        )

    # ---- main-thread event pump ---------------------------------------------------

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "connected":
                    self._on_connected(event[1])
                elif kind == "connect_error":
                    self._on_connect_error(event[1])
                elif kind == "packet":
                    _, from_id, _portnum, text = event
                    self._log(f"[{time.strftime('%H:%M:%S')}] {from_id}: {text}")
                elif kind == "lost":
                    if self.connected:
                        self._disconnect()
                        self._log("!! Device connection lost.")
        except queue.Empty:
            pass
        except Exception as exc:
            self._log(f"!! {exc}")
        self.root.after(self.POLL_MS, self._poll_events)

    # ---- nodes ----------------------------------------------------------------------

    def _refresh_nodes_now(self):
        if not (self.connected and self.interface):
            return
        selected = self.node_tree.selection()
        selected_id = selected[0] if selected else None

        children = self.node_tree.get_children()
        if children:
            self.node_tree.delete(*children)

        self.node_tree.insert(
            "", tk.END, iid=BROADCAST, values=("everyone (broadcast)", BROADCAST)
        )
        for node_id, node in self.interface.nodes.items():
            user = node.get("user", {})
            name = user.get("longName") or user.get("shortName") or node_id
            self.node_tree.insert("", tk.END, iid=node_id, values=(name, node_id))

        if selected_id and self.node_tree.exists(selected_id):
            self.node_tree.selection_set(selected_id)
        else:
            self.node_tree.selection_set(BROADCAST)

    def _refresh_nodes(self):
        try:
            self._refresh_nodes_now()
        except Exception:
            pass  # device may have gone away; retry on the next tick
        self.root.after(self.NODE_REFRESH_MS, self._refresh_nodes)

    def _on_node_select(self, _event):
        selection = self.node_tree.selection()
        if selection:
            self.dest_id = selection[0]
            self._update_dest_label()

    def _select_broadcast(self):
        self.dest_id = BROADCAST
        if self.node_tree.exists(BROADCAST):
            self.node_tree.selection_set(BROADCAST)
        self._update_dest_label()

    def _update_dest_label(self):
        if self.dest_id == BROADCAST:
            self.dest_var.set("To: everyone (broadcast)")
        elif self.node_tree.exists(self.dest_id):
            name = self.node_tree.item(self.dest_id, "values")[0]
            self.dest_var.set(f"To: {name} ({self.dest_id})")
        else:
            self.dest_var.set(f"To: {self.dest_id}")

    # ---- messaging --------------------------------------------------------------------

    def _send(self):
        if not self.connected:
            messagebox.showwarning("Not connected", "Connect to a device first.")
            return
        text = self.msg_entry.get().strip()
        if not text:
            return
        try:
            self.interface.sendText(text, destinationId=self.dest_id)
        except Exception as exc:
            self._log(f"!! Send failed: {exc}")
            return
        who = "everyone" if self.dest_id == BROADCAST else self.dest_id
        self._log(f"[{time.strftime('%H:%M:%S')}] me -> {who}: {text}")
        self.msg_entry.delete(0, tk.END)

    # ---- helpers ------------------------------------------------------------------------

    def _log(self, line):
        self.chat.config(state=tk.NORMAL)
        self.chat.insert(tk.END, line + "\n")
        self.chat.see(tk.END)
        self.chat.config(state=tk.DISABLED)

    def _set_status(self, text):
        self.status_var.set(text)

    def _on_close(self):
        self._disconnect()
        self.root.destroy()


# ---------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Meshtastic chat client (GUI by default, --cli for terminal)."
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port, e.g. /dev/ttyUSB0 or /dev/ttyACM0. Omit to auto-detect.",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run the terminal-only mode instead of the GUI.",
    )
    parser.add_argument(
        "--send",
        metavar="TEXT",
        default=None,
        help="Terminal mode only: send a broadcast text message, then exit.",
    )
    args = parser.parse_args()

    if args.cli or args.send:
        run_cli(args.port, args.send)
        return

    if not TK_OK:
        print(
            "tkinter is not available.\n"
            "Install it (e.g. 'sudo apt install python3-tk') or use --cli mode.",
            file=sys.stderr,
        )
        sys.exit(1)

    root = tk.Tk()
    MeshtasticGUI(root, initial_port=args.port)
    root.mainloop()


if __name__ == "__main__":
    main()
