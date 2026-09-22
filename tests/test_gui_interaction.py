"""Native Tk interaction/resize regressions. No SDK, downloads or target hardware."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('gui_tool',Path(__file__).parents[1]/'keil_port_tool.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def children(widget):
    for child in widget.winfo_children():
        yield child
        yield from children(child)

class GuiTests(unittest.TestCase):
    def setUp(self):
        if m.tk is None:
            self.skipTest('Tk unavailable')
        try:
            with patch.object(m,'_find_projects',return_value=[]):
                self.gui=m.KeilPortGUI(scaling=96/72)
        except m.tk.TclError as exc:
            if 'display' in str(exc).lower():self.skipTest(str(exc))
            raise
        self.gui.root.geometry('980x650')
        self.gui.root.update()

    def tearDown(self):
        if hasattr(self,'gui'):self.gui._close()

    def test_navigation_and_all_file_views_remain_visible(self):
        g=self.gui
        for size in ('980x650','1180x800'):
            g.root.geometry(size);g.root.update()
            for index,button in enumerate(g.nav_buttons):
                button.invoke();g.root.update()
                self.assertEqual(g.notebook.index(g.notebook.select()),index)
                self.assertIn('selected',button.state())
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),g.bottom_frame.winfo_rooty())
            pages=[(0,None,g.add_tree),(1,None,g.freertos_tree),(2,None,g.rtthread_tree),
                (3,None,g.lvgl_tree),(4,None,g.fatfs_tree),
                (6,0,g.rtt_tree),(6,1,g.littlefs_tree),(6,2,g.cmsis_dsp_tree),
                (7,0,g.lwip_tree),(7,1,g.tinyusb_tree)]
            for index,inner,tree in pages:
                g.nav_buttons[index].invoke()
                if inner is not None:
                    (g.extension_buttons if index==6 else g.network_buttons)[inner].invoke()
                g.root.update()
                self.assertTrue(tree.tree.winfo_ismapped())
                self.assertGreaterEqual(tree.tree.winfo_height(),140,(size,index,inner))
            self.assertLessEqual(g.execute_button.winfo_rootx()+g.execute_button.winfo_width(),
                                 g.root.winfo_rootx()+g.root.winfo_width())
            before=g.tinyusb_tree.tree.winfo_height()
            g.toggle_log();g.root.update()
            self.assertTrue(g.log_frame.winfo_ismapped())
            self.assertEqual(g.tinyusb_tree.tree.winfo_height(),before)
            g.toggle_log();g.root.update()

    def test_tick_controls_keyboard_conflicts(self):
        g=self.gui
        g.nav_buttons[2].invoke();g.root.update()
        button=next(w for w in children(g.root) if isinstance(w,m.ttk.Checkbutton)
                    and str(w.cget('variable'))==str(g.rtthread_enabled))
        button.invoke();g.root.update()
        self.assertTrue(g.rtthread_enabled.get())
        self.assertIn('✓',g.nav_buttons[2].cget('text'))
        button.focus_force();g.root.update()
        button.event_generate('<KeyPress-space>');button.event_generate('<KeyRelease-space>');g.root.update()
        self.assertFalse(g.rtthread_enabled.get())
        g.rtthread_enabled.set(True);g.freertos_enabled.set(True)
        self.assertTrue(g.execute_button.instate(['disabled']))
        self.assertIn('RTOS',g.selection_summary.get())
        g.freertos_enabled.set(False)
        self.assertFalse(g.execute_button.instate(['disabled']))
        self.assertIn('KPS.Check.indicator',str(m.ttk.Style(g.root).layout('Modern.TCheckbutton')))
        for variable,convert,legacy in ((g.fatfs_mode,g._fatfs_mode_value,'FreeRTOS / CMSIS-V2'),
            (g.littlefs_mode,g._littlefs_mode_value,'FreeRTOS'),(g.lwip_mode,g._lwip_mode_value,'FreeRTOS')):
            for label in ('RTOS（工程内核）',legacy):
                variable.set(label);self.assertEqual(convert(),'rtos')

    def test_tree_partial_refresh_preset_and_expand(self):
        g=self.gui;t=g.add_tree
        base=Path.cwd()/'gui_fixture'
        files=[(base/'core/a.c','A'),(base/'core/b.c','B')]
        t.set_files(base,files);g.root.update()
        group=t.tree.get_children('')[0]
        leaf=t.tree.get_children(group)[0]
        t.tree.selection_set(leaf);t.tree.focus(leaf);t.tree.focus_force();g.root.update()
        t.tree.event_generate('<space>');g.root.update()
        self.assertEqual(len(t.checked_paths()),1)
        self.assertIsNone(t._state[group])
        self.assertEqual(str(t.tree.item(group,'image')[0]),str(t._images['mixed']))
        self.assertIn('1 / 2',t.summary_var.get())
        t.set_files(base,files+[(base/'core/c.c','C')])
        self.assertEqual(t.selection_keys(),['core/b.c','core/c.c'])
        t.apply_selection_keys(['core/a.c'])
        self.assertEqual(t.selection_keys(),['core/a.c'])
        group=t.tree.get_children('')[0];g.root.update()
        box=t.tree.bbox(group);before=t.checked_paths()
        arrow=next(x for x in range(box[0],box[0]+40)
                   if 'indicator' in t.tree.identify_element(x,box[1]+box[3]//2))
        t.tree.event_generate('<ButtonPress-1>',x=arrow,y=box[1]+box[3]//2)
        t.tree.event_generate('<ButtonRelease-1>',x=arrow,y=box[1]+box[3]//2);g.root.update()
        self.assertEqual(before,t.checked_paths())
        t.select_all(False);self.assertFalse(t.checked_paths())
        t.select_all(True);self.assertEqual(len(t.checked_paths()),3)
        t.clear();self.assertTrue(t.empty_label.winfo_manager()=='place')

    def test_settings_can_scroll_to_last_options(self):
        g=self.gui;g.nav_buttons[5].invoke();g.root.update()
        self.assertLess(g.settings_canvas.yview()[1],1)
        g.settings_canvas.yview_moveto(1);g.root.update()
        options=[w for w in children(g.settings_body) if isinstance(w,m.ttk.Checkbutton)]
        last=options[-1]
        self.assertGreaterEqual(last.winfo_rooty(),g.settings_canvas.winfo_rooty())
        self.assertLessEqual(last.winfo_rooty()+last.winfo_height(),
                             g.settings_canvas.winfo_rooty()+g.settings_canvas.winfo_height())

    def test_scaled_layouts(self):
        for scale in (1.25,1.5):
            self.gui._close()
            with patch.object(m,'_find_projects',return_value=[]):
                self.gui=m.KeilPortGUI(scaling=(96/72)*scale)
            g=self.gui
            g.root.geometry('%dx%d'%(round(980*scale),round(650*scale)))
            for index in (0,1,2,3,4,6,7):
                g.nav_buttons[index].invoke();g.root.update()
                self.assertLessEqual(g.nav_buttons[-1].winfo_rooty()+g.nav_buttons[-1].winfo_height(),
                                     g.bottom_frame.winfo_rooty())
                self.assertLessEqual(g.execute_button.winfo_rootx()+g.execute_button.winfo_width(),
                                     g.root.winfo_rootx()+g.root.winfo_width())
            for button in g.network_buttons:
                button.invoke();g.root.update()
            self.assertGreaterEqual(g.tinyusb_tree.tree.winfo_height(),140*scale)
            controls=[w for w in children(g.tinyusb_tree.master) if isinstance(w,m.ttk.Checkbutton)]
            edge=g.tinyusb_tree.master.winfo_rootx()+g.tinyusb_tree.master.winfo_width()
            self.assertTrue(controls)
            for control in controls:
                self.assertLessEqual(control.winfo_rootx()+control.winfo_width(),edge)

if __name__=='__main__':unittest.main(verbosity=2)
