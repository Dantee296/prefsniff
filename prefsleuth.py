#!/usr/bin/env python

import sys
import subprocess
import tempfile
import difflib
import plistlib
import os
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
import os.path
from Queue import Queue
from Queue import Empty as QueueEmpty

import xml.etree.ElementTree as ET


"""
Pulling out <dict> from xml plist:
import xml.etree.ElementTree as ET
>>> ET.tostring(root)
'<plist version="1.0">\n<dict>\n\t<key>1</key>\n\t<string>a</string>\n\t<key>2</key>\n\t<string>b</string>\n</dict>\n</plist>'
>>> root.find("dict")
<Element 'dict' at 0x106295a90>
>>> ET.tostring(root.find("dict"))
'<dict>\n\t<key>1</key>\n\t<string>a</string>\n\t<key>2</key>\n\t<string>b</string>\n</dict>\n'
"""


"""
To create the following foo.bar preference under the key "mydict":
{
    mydict = {
        enabled=1;
    };
}
We can do any of the following:
- Don't specify the type at all, and use an xml represenation of the dictionary
defaults write foo.bar mydict "<dict><key>enabled</key><string>1</string></dict>"
- Specify the type as -dict, followed by the key you want to add to mydict:
defaults write foo.bar mydict -dict mysubdict "<dict><key>somekey</key><string>1</string></dict>"
defaults write foo.bar mydict -dict enabled "<integer>1</integer>"

This creates or replaces 'mydict'
- Specify -dict-add in order to add to create mydict or add to it if it already exists
defaults write foo.bar mydict -dict-add mysubdict "<dict><key>somekey</key><string>1</string></dict>"
defaults write foo.bar mydict -dict-add enabled "<integer>1</integer>"
"""


class PSChangeTypeString(object):


    def __init__(self, domain, key, value=None):
        self.action = "write"
        self.domain = domain
        self.key = key
        self.type = "-string"
        self.value = value

    def __str__(self):
        # defaults action domain key
        fmt = "defaults %s %s %s"
        cmd = (fmt % (self.action, self.domain, self.key))
        # some commands work without a type and are easier that way
        if not self.type == None:
            cmd += " %s" % self.type
        if not self.value == None:
            cmd += " %s" % self.value
        return cmd


class PSChangeTypeKeyDeleted(PSChangeTypeString):

    def __init__(self, domain, key):
        super(PSChangeTypeKeyDeleted, self).__init__(domain, key, value=None)
        self.action = "delete"
        self.type = None


class PSChangeTypeFloat(PSChangeTypeString):

    def __init__(self, domain, key, value):
        super(PSChangeTypeFloat, self).__init__(domain, key)
        self.type = "-float"
        if not isinstance(value, float):
            raise Exception("Float required for -float prefs change.")
        self.value = str(value)


class PSChangeTypeInt(PSChangeTypeString):

    def __init__(self, domain, key, value):
        super(PSChangeTypeInt, self).__init__(domain, key)
        self.type = "-int"
        if not isinstance(value, int):
            raise Exception("Integer required for -int prefs change.")
        self.value = str(value)


class PSChangeTypeBool(PSChangeTypeString):

    def __init__(self, domain, key, value):
        super(PSChangeTypeBool, self).__init__(domain, key)
        self.type = "-bool"
        if not isinstance(value, bool):
            raise Exception("Boolean required for -bool prefs change.")
        self.value = str(value)


class PSChangeTypeDict(PSChangeTypeString):

    def __init__(self, domain, key, value={}):
        super(PSChangeTypeDict, self).__init__(domain, key)
        # We have to omit the -dict type
        # And just let defaults interpet the xml dict string
        self.type = None
        # TODO: not sure what to do here. I want to sanity check we got handed a dict
        # unless we've been subclassed.
        self.value = value
        if isinstance(value, dict):
            self.value = "\'%s\'" % self.to_xmlfrag(value)


    def to_xmlfrag(self, value):

        # create plist-serialized form of changed objects
        plist_str = plistlib.writePlistToString(value)
        # remove newlines and tabs from plist
        plist_str = "".join([line.strip() for line in plist_str.splitlines()])
        # parse the plist xml doc, so we can pull out the important parts.
        tree = ET.ElementTree(ET.fromstring(plist_str))
        # get elements inside <plist> </plist>
        children = list(tree.getroot())
        # there can only be one element inside <plist>
        if len(children) < 1:
            raise Exception("Empty dictionary for key %s" % str(self.key))
        if len(children) > 1:
            raise Exception(
                "Something went wrong for key %s. Can only support one dictionary for dict change." % self.dict_key)
        # extract changed objects out of the plist element
        xmlfrag = ET.tostring(children[0])
        return xmlfrag

class PSChangeTypeArray(PSChangeTypeDict):
    def __init__(self,domain,key,value):
        super(PSChangeTypeArray,self).__init__(domain,key)
        if not isinstance(value,list):
            raise Exception("PSChangeTypeArray requires a list value type.")
        self.type=None
        self.value="\'%s\'" % self.to_xmlfrag(value)


class PSChangeTypeDictAdd(PSChangeTypeDict):

    def __init__(self, domain, key, subkey, value):
        super(PSChangeTypeDictAdd, self).__init__(domain, key)
        self.type = "-dict-add"
        self.subkey = subkey
        self.value = self.__generate_value_string(subkey, value)

    def __generate_value_string(self, subkey, value):
        xmlfrag = self.to_xmlfrag(value)
        valuestring = "%s \'%s\'" % (subkey, xmlfrag)
        return valuestring


    # def __str__(self):
    #     # hopefully generate something like:
    #     #"dict-key 'val-to-add-to-dict'"
    #     # so we can generate a command like
    #     # defaults write foo -dict-add dict-key 'value'
    #     xmlfrag = self.xmlfrag
    #     return " %s '%s'" % (self.dict_key, xmlfrag)

class PSChangeTypeArrayAdd(PSChangeTypeArray):
    def __init__(self,domain,key,value):
        super(PSChangeTypeArrayAdd,self).__init__(domain,key,value)
        self.type="-array-add"
        self.value=self.__generate_value_string(value)

    def __generate_value_string(self,value):
        valuestring=""
        for v in value:
            xmlfrag=self.to_xmlfrag(v)
            valuestring+="\'%s\' " % xmlfrag
        return valuestring

class PSChangeTypeData(PSChangeTypeString):
    def __init__(self,domain,key,value):
        raise Exception("%s not implemented" % self.__class__.__name__)

class PSChangeTypeDate(PSChangeTypeString):
    def __init__(self,domain,key,value):
        raise Exception("%s not implemented" % self.__class__.__name__)


class PrefSleuth(object):
    CHANGE_TYPES = {int: PSChangeTypeInt,
                    float: PSChangeTypeFloat,
                    str: PSChangeTypeString,
                    bool: PSChangeTypeBool,
                    dict: PSChangeTypeDict,
                    list: PSChangeTypeArray}

    def __init__(self, plistpath):
        self.plist_dir = os.path.dirname(plistpath)
        self.plist_base = os.path.basename(plistpath)
        self.pref_domain = os.path.splitext(self.plist_base)[0]

        self.plistpath = plistpath
        plist_to_xml = ["plutil", "-convert",
                        "xml1", "-o", "-", "%s" % plistpath]
        tempfile1 = tempfile.mkstemp()
        tempfile2 = tempfile.mkstemp()
        self.execute(plist_to_xml, stdout=tempfile1[0])
        pref1 = plistlib.readPlist(tempfile1[1])
        self._wait_for_prefchange()
        self.execute(plist_to_xml, stdout=tempfile2[0])
        pref2 = plistlib.readPlist(tempfile2[1])
        added, removed, modified, same = self._dict_compare(pref1, pref2)
        self.removed = {}
        self.added = {}
        self.modified = {}

        # At this stage, added and removed would be
        # a key:value added or removed from the top-level
        #<dict> of the plist
        if len(added):
            self.added = added
        if len(removed):
            self.removed = removed
        if len(modified):
            self.modified = modified

        self.commands = self._generate_commands()
        self.diff = ""
        for line in self._unified_diff(tempfile1[1], tempfile2[1]):
            self.diff += line

    def _dict_compare(self, d1, d2):
        d1_keys = set(d1.keys())
        d2_keys = set(d2.keys())
        intersect_keys = d1_keys.intersection(d2_keys)
        added_keys = d2_keys - d1_keys
        added = {o: d2[o] for o in added_keys}
        removed = d1_keys - d2_keys
        modified = {o: (d1[o], d2[o])
                    for o in intersect_keys if d1[o] != d2[o]}
        
        same = set(o for o in intersect_keys if d1[o] == d2[o])
        return added, removed, modified, same

    def _list_compare(self,list1,list2):
        list_diffs={"same":False,"append_to_l1":None,"subtract_from_l1":None}
        if list1==list2:
            list_diffs["same"]=True
            return list_diffs
        if len(list2) > len(list1):
            if list1==list2[:len(list1)]:
                list_diffs["append_to_l1"]=list2[len(list1):]

            return list_diffs
        elif len(list1)>len(list2):
            if list2==list1[:len(list2)]:
                list_diffs["subtract_from_l1"]=list1[len(list2):]
            
            return list_diffs

        return list_diffs



    def _unified_diff(self, fromfile, tofile):
        fromlines = open(fromfile, "rb").readlines()
        tolines = open(tofile, "rb").readlines()
        return difflib.unified_diff(fromlines, tolines, fromfile, tofile)

    def _wait_for_prefchange(self):
        event_queue = Queue()
        event_handler = PrefChangedEventHandler(self.plist_base, event_queue)
        observer = Observer()
        observer.schedule(event_handler, self.plist_dir, recursive=False)
        observer.start()
        pref_updated = False
        try:
            while not pref_updated:
                try:
                    event = event_queue.get(True, 0.5)
                    if event[0] == "moved" and os.path.basename(event[1].dest_path) == self.plist_base:
                        pref_updated = True
                    if event[0] == "modified" and os.path.basename(event[1].src_path) == self.plist_base:
                        pref_updated = True
                    if event[0] == "created" and os.path.basename(event[1].src_path) == self.plist_base:
                        pref_updated = True
                except QueueEmpty:
                    pass
        except KeyboardInterrupt:
            observer.stop()
            raise
        observer.stop()
        observer.join()

    def _change_type_lookup(self, obj):
        try:
            cls = self.CHANGE_TYPES[obj]
        except (KeyError, TypeError):
            cls = self._change_type_slow_search(obj)

        return cls

    def _change_type_slow_search(self, obj):
        for cls, change_type in self.CHANGE_TYPES.items():
            if isinstance(obj, cls):
                return change_type

        return None

    def _generate_commands(self):
        commands = []
        # sub-dictionaries that must be rewritten because
        # something was removed.
        rewrite_dictionaries = {}

        #we can only append to existing arrays
        #if an array changes in any other way, we have to rewrite it 
        rewrite_lists={}
        domain = self.pref_domain
        for k, v in self.added.items():

            change_type = self._change_type_lookup(v)
            change = change_type(domain, k, v)
            commands.append(str(change))
        for k in self.removed:
            change = PSChangeTypeKeyDeleted(domain, k)
            commands.append(str(change))
        for key, val in self.modified.items():
            if isinstance(val[1], dict):
                added, removed, modified, same = self._dict_compare(val[
                                                                    0], val[1])
                if len(removed):
                    # There is no -dict-delete so we have to
                    # rewrite this sub-dictionary
                    rewrite_dictionaries[key] = val[1]
                    continue
                for subkey, subval in added.items():
                    change = PSChangeTypeDictAdd(domain, key, subkey, subval)
                    commands.append(str(change))
                for subkey, subval_tuple in modified.items():
                    change = PSChangeTypeDictAdd(
                        domain, key, subkey, subval_tuple[1])
                    commands.append(str(change))
            elif isinstance(val[1],list):
                list_diffs=self._list_compare(val[0],val[1])
                if list_diffs["same"]:
                    continue
                elif list_diffs["append_to_l1"]:
                    append=list_diffs["append_to_l1"]
                    change=PSChangeTypeArrayAdd(domain,key,append)
                    commands.append(str(change))
                else:
                    rewrite_lists[key]=val[1]
            else:
                # for modified keys that aren't dictionaries, we treat them
                # like adds
                change_type = self._change_type_lookup(val[1])
                change = change_type(domain, key, val[1])
                commands.append(str(change))
        for key, val in rewrite_dictionaries.items():
            change = PSChangeTypeDict(domain, key, val)
            commands.append(str(change))

        for key, val in rewrite_lists.items():
            change = PSChangeTypeArray(domain,key,val)
            commands.append(str(change))

        return commands

    def execute(self, args, stdout=None):
        subprocess.check_call(args, stdout=stdout)


class PrefsWatcher(object):

    def __init__(self, prefsdir):
        self.prefsdir = prefsdir
        self._watch_prefsdir()

    def _watch_prefsdir(self):
        event_queue = Queue()
        event_handler = PrefChangedEventHandler(None, event_queue)
        observer = Observer()
        observer.schedule(event_handler, self.prefsdir, recursive=False)
        observer.start()
        while True:
            try:
                event_queue.get(True, 0.5)
            except QueueEmpty:
                pass
            except KeyboardInterrupt:
                break
        observer.stop()
        observer.join()


class PrefChangedEventHandler(FileSystemEventHandler):

    def __init__(self, file_base_name, event_queue):
        super(self.__class__, self).__init__()
        if file_base_name == None:
            file_base_name = ""
        self.file_base_name = file_base_name
        self.event_queue = event_queue
        print("Watching prefs file: %s" % self.file_base_name)

    def on_created(self, event):
        if not self.file_base_name in os.path.basename(event.src_path):
            return
        self.event_queue.put(("created", event))

    def on_deleted(self, event):
        if not self.file_base_name in os.path.basename(event.src_path):
            return
        self.event_queue.put(("deleted", event))

    def on_modified(self, event):
        if not self.file_base_name in os.path.basename(event.src_path):
            return
        self.event_queue.put(("modified", event))

    def on_moved(self, event):
        if not self.file_base_name in os.path.basename(event.src_path):
            return
        self.event_queue.put(("moved", event))


def main(plistpath, monitor_dir_events=False, print_diffs=False):
    if monitor_dir_events:
        PrefsWatcher(plistpath)
    else:
        while True:
            try:
                diffs = PrefSleuth(plistpath)
            except KeyboardInterrupt:
                print("Exiting.")
                exit(0)
            if print_diffs:
                print diffs.diff
            
            for cmd in diffs.commands:
                print cmd


def test_dict_add(domain, key, subkey, value):
    prefchange = PSChangeTypeDictAdd(domain, key, subkey, value)
    print str(prefchange)


def test_dict_add_dict(args):
    domain = args[0]
    key = args[1]
    subkey = args[2]
    value = {"mykey1": 2.0, "mykey2": 7}
    test_dict_add(domain, key, subkey, value)


def test_dict_add_float(args):
    domain = args[0]
    key = args[1]
    subkey = args[2]
    value = 2.0
    test_dict_add(domain, key, subkey, value)


def test_write_dict(args):
    domain = args[0]
    key = args[1]
    value = {"dictkey1": 2.0, "dictkey2": {"subkey": '7'}}
    prefchange = PSChangeTypeDict(domain, key, value)
    print str(prefchange)

if __name__ == '__main__':
    if "test-dict-add-float" == sys.argv[1]:
        test_dict_add_float(sys.argv[2:])
        exit(0)

    if "test-dict-add-dict" == sys.argv[1]:
        test_dict_add_dict(sys.argv[2:])
        exit(0)

    if "test-write-dict" == sys.argv[1]:
        test_write_dict(sys.argv[2:])
        exit(0)

    plistpath = sys.argv[1]
    monitor_dir_events = False
    if(os.path.isdir(plistpath)):
        print("Watching events for directory: %s" % plistpath)
        monitor_dir_events = True
    main(plistpath, monitor_dir_events)
