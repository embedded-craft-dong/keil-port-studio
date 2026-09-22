import importlib.util
from pathlib import Path

root = Path(__file__).parents[1]
tool = root / 'keil_port_tool.py'
if not tool.exists():
    tool = root / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool_gui_smoke', tool)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

gui = module.KeilPortGUI(scaling=96/72)
gui.root.geometry('980x650')
gui.root.update()
assert len(gui.notebook.tabs()) == 8
assert gui.add_tree.tree.winfo_exists()
assert gui.freertos_tree.tree.winfo_exists()
assert gui.rtthread_tree.tree.winfo_exists()
assert gui.lvgl_tree.tree.winfo_exists()
assert gui.fatfs_tree.tree.winfo_exists()
assert gui.rtt_tree.tree.winfo_exists()
assert gui.littlefs_tree.tree.winfo_exists()
assert gui.cmsis_dsp_tree.tree.winfo_exists()
assert gui.lwip_tree.tree.winfo_exists()
assert gui.tinyusb_tree.tree.winfo_exists()
heights = []
for index, tree in enumerate((gui.add_tree, gui.freertos_tree, gui.rtthread_tree, gui.lvgl_tree, gui.fatfs_tree)):
    gui.notebook.select(index)
    gui.root.update()
    heights.append(tree.tree.winfo_height())
    print('tab', index, [(type(w).__name__, w.winfo_height(), w.grid_info().get('row'))
                         for w in tree.master.winfo_children()])
print('sizes:', gui.root.winfo_height(), gui.header_frame.winfo_height(),
      gui.main_frame.winfo_height(), gui.project_card.winfo_height(),
      gui.notebook.winfo_height(), gui.bottom_frame.winfo_height(), heights)
assert min(heights) >= 140, heights
gui.notebook.select(6)
extension_heights = []
for index, tree in enumerate((gui.rtt_tree, gui.littlefs_tree, gui.cmsis_dsp_tree)):
    gui.extensions_notebook.select(index)
    gui.root.update()
    extension_heights.append(tree.tree.winfo_height())
assert min(extension_heights) >= 100, extension_heights
gui.notebook.select(7)
network_heights = []
for index, tree in enumerate((gui.lwip_tree, gui.tinyusb_tree)):
    gui.network_usb_notebook.select(index)
    gui.root.update()
    network_heights.append(tree.tree.winfo_height())
assert min(network_heights) >= 100, network_heights
gui.toggle_log()
gui.root.update()
assert gui.log_frame.winfo_ismapped()
gui.toggle_log()
gui.root.update()
assert not gui.log_frame.winfo_ismapped()
gui.root.withdraw()
gui.root.destroy()
print('GUI smoke test passed; tree heights:', heights, 'extensions:', extension_heights,
      'network/usb:', network_heights)
