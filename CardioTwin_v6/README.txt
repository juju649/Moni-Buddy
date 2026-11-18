CARDIOTWIN  (Team MoniBuddy, HackEdge 2026 Track 1)
Experimental student prototype. Not a medical device. Does not diagnose.

SET UP IN VS CODE (once)
1. Unzip the folder and open it in VS Code (File, Open Folder).
2. Open a terminal in VS Code and run:
     pip install -r requirements.txt
3. Run the dashboard:
     python -m streamlit run dashboard.py
   Your browser opens by itself.

WHAT WORKS RIGHT NOW (no hardware)
- Live monitor, "Recorded capture" mode: replays your real AFE4950 export as a stream
  with heart rate, HRV, signal quality, digital twin baseline and offline queue.
- Recording review: sliders, filtering toggle, R-peak markers.
- Moni-Buddy export: makes events.json for the Moni-Buddy backend.
- Command line version of the manual: python analyze_signals.py

WHEN YOU PLUG IN THE BOARD
1. Open the TI Bio-Sensing GUI. Import your AFE settings file. Start one capture.
   Then CLOSE the GUI so the USB port is free. Do not unplug the EVM power.
2. Plug the AFE micro-USB cable into the laptop. Put electrodes and PPG sensor on.
3. In the dashboard open "Connect AFE". Press Scan ports, then Test AFE ports,
   then Start live capture.
4. Switch the sidebar to "Live AFE4950 (USB)". The monitor now shows live data.
5. Save capture as CSV keeps what you recorded. It then shows up in the recording list.

IMPORTANT, HONEST STATUS
Live mode follows the capture protocol in TI's own scripts and passes tests against a
simulated AFE. It has NOT been run on the real board yet. If it does not stream, the
Connect AFE tab shows the raw lines it received. Send those and it can be fixed fast.
If the PPG slot is not found, load your *_FIFO_SEQ.cfg file in Advanced settings.

FILES
dashboard.py       the app
pipeline.py        filters, beat detection, quality score
twin.py            digital twin: baseline, history, offline queue
afe_serial.py      live USB reader for the AFE4950
source.py          replay and live sources
analyze_signals.py manual style analysis of a CSV
pushevents.py      Moni-Buddy event builder
data/              your capture (AFE4950_CAPTURED_DATA.csv)

SENDING DATA TO THE MONI-BUDDY BACKEND
python edge_publisher.py --url http://BACKEND_IP:8000 --replay data/AFE4950_CAPTURED_DATA.csv
Remove --replay for live AFE data. See FRIEND_INTEGRATION.txt.
ROUND3_PROMPT.txt holds the prompt for Claude Code.

PROCESSING ON THE SK-AM62B (no Ethernet, no numpy on the board)
board_processor.py runs on the board with plain Python. It filters the recorded ECG and PPG, finds heartbeats,
scores signal quality and prints one finished result per second. The dashboard reads those lines over the debug
micro-USB cable and only draws them.
1. Copy board_processor.py and data/AFE4950_CAPTURED_DATA.csv into a folder named ct on the microSD boot partition.
2. On the board console: python3 /boot/ct/board_processor.py --loop > $(tty) 2>&1 &
3. Close PuTTY. In the dashboard open the SK-AM62B board tab, pick the COM port, press Connect.
4. In the sidebar choose "SK-AM62B board (processed on the board)".
No board yet? Press "Run a simulated board on this laptop". The header then says SIMULATED.
The board code matches pipeline.py on the laptop. Breathing rate is not computed on the board.


NEWEST CHANGES (Nov 17)
- Cleaning: first 2 s and last 1 s of a recording are trimmed in the live monitor, replay and board path. Recording review is untrimmed.
- Beats more than 20 percent off the median interval are ignored for heart rate and HRV. The count is shown.
- Heart rate shows only when signal quality is 75 or higher, and is the median of the last 5 results.
- board_processor.py plays every recording CSV in /boot/ct in order. Copy the new board_processor.py to the board to get these fixes.
- SK-AM62B board tab now has the backend sender and the ESP32 watch server. See FRIEND_INTEGRATION.txt and esp32_watch.ino.
- The board replays a recording. Say: processed live on the board, from a recording.
