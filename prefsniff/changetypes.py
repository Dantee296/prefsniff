from shlex import quote as cmd_quote
import plistlib
import xml.etree.ElementTree as ET
import inspect
from .exceptions import (
    PSChangeTypeException,
    PSChangeTypeNotImplementedException
)


class PSChangeTypeBase:
    COMMAND = "defaults"
    ACTION = None
    TYPE = None

    def __init__(self, domain, byhost, key, value=None):
        if self.ACTION is None:
            raise NotImplementedError("Need to sublclass and override cls.ACTION")
        self.command = self.COMMAND
        self.action = self.ACTION
        self.domain = domain
        self.key = key
        if self.TYPE is None:
            raise NotImplementedError(
                "self.TYPE is None")
        self.type = self.TYPE
        self.value = value
        self.byhost = byhost

    def _quote(self, value, quote=True):
        if quote:
            value = cmd_quote(value)
        return value

    def argv(self, quote=True):
        argv = [self.command]
        if self.byhost:
            argv.append("-currentHost")
        argv.append(self._quote(self.action, quote=quote))
        argv.append(self._quote(self.domain, quote=quote))
        argv.append(self._quote(self.key, quote=quote))
        if self.type is not None:
            type_arg = f"-{self.type}"
            argv.append(self._quote(type_arg, quote=quote))
        value_argv = self._value_argv(quote=quote)

        if value_argv is not None:
            argv.extend(value_argv)

        return argv

    def _value_argv(self, quote=True):

        value_argv = None
        if self.value is not None:
            if isinstance(self.value, (list, tuple)):
                value_argv = [self._quote(v, quote=quote) for v in self.value]
            else:
                value_argv = [self._quote(self.value, quote=quote)]

        return value_argv

    def shell_command(self):
        argv = self.argv(quote=True)
        command = ' '.join(argv)
        return command


class PSChangeTypeString(PSChangeTypeBase):
    ACTION = "write"
    TYPE = "string"


class PSChangeTypeKeyDeleted(PSChangeTypeString):
    ACTION = "delete"
    TYPE = None


class PSChangeTypeFloat(PSChangeTypeString):
    TYPE = "float"

    def __init__(self, domain, byhost, key, value):
        super().__init__(domain, byhost, key)
        self.type = "-float"
        if not isinstance(value, float):
            raise PSChangeTypeException(
                "Float required for -float prefs change.")
        self.value = str(value)


class PSChangeTypeInt(PSChangeTypeString):
    TYPE = "int"

    def __init__(self, domain, byhost, key, value):
        super().__init__(domain, byhost, key)
        if not isinstance(value, int):
            raise PSChangeTypeException(
                "Integer required for -int prefs change.")
        self.value = str(value)


class PSChangeTypeBool(PSChangeTypeString):

    TYPE = "bool"

    def __init__(self, domain, byhost, key, value):
        super().__init__(domain, byhost, key)
        if not isinstance(value, bool):
            raise PSChangeTypeException(
                "Boolean required for -bool prefs change.")
        self.value = str(value)


class PSChangeTypeDict(PSChangeTypeString):
    # We have to omit the -dict type
    # And just let defaults interpet the xml dict string
    TYPE = None

    def __init__(self, domain, byhost, key, value={}):
        super().__init__(domain, byhost, key)

        # TODO: not sure what to do here. I want to sanity check we got handed a dict
        # unless we've been subclassed.
        self.value = value
        if isinstance(value, dict):
            self.value = self.to_xmlfrag(value)

    def to_xmlfrag(self, value):

        # create plist-serialized form of changed objects
        plist_str = plistlib.dumps(value, fmt=plistlib.FMT_XML).decode('utf-8')

        # remove newlines and tabs from plist
        plist_str = "".join([line.strip() for line in plist_str.splitlines()])
        # parse the plist xml doc, so we can pull out the important parts.
        tree = ET.ElementTree(ET.fromstring(plist_str))
        # get elements inside <plist> </plist>
        children = list(tree.getroot())
        # there can only be one element inside <plist>
        if len(children) < 1:
            fn = inspect.getframeinfo(inspect.currentframe()).function
            raise PSChangeTypeException(
                "%s: Empty dictionary for key %s" % (fn, str(self.key)))
        if len(children) > 1:
            fn = inspect.getframeinfo(inspect.currentframe()).function
            raise PSChangeTypeException(
                "%s: Something went wrong for key %s. Can only support one dictionary for dict change." % (fn, self.dict_key))
        # extract changed objects out of the plist element
        # python 2 & 3 compat
        # https://stackoverflow.com/questions/15304229/convert-python-elementtree-to-string#15304351
        xmlfrag = ET.tostring(children[0]).decode()
        return xmlfrag


class PSChangeTypeArray(PSChangeTypeDict):
    TYPE = None

    def __init__(self, domain, byhost, key, value):
        super().__init__(domain, byhost, key)
        if not isinstance(value, list):
            raise PSChangeTypeException(
                "PSChangeTypeArray requires a list value type.")
        self.value = self.to_xmlfrag(value)


class PSChangeTypeDictAdd(PSChangeTypeDict):
    TYPE = "dict-add"

    def __init__(self, domain, byhost, key, subkey, value):
        super().__init__(domain, byhost, key)
        self.subkey = subkey
        self.value = self._generate_value_string(subkey, value)

    def _generate_value_string(self, subkey, value):
        xmlfrag = self.to_xmlfrag(value)
        return (subkey, xmlfrag)

    # def __str__(self):
    #     # hopefully generate something like:
    #     #"dict-key 'val-to-add-to-dict'"
    #     # so we can generate a command like
    #     # defaults write foo -dict-add dict-key 'value'
    #     xmlfrag = self.xmlfrag
    #     return " %s '%s'" % (self.dict_key, xmlfrag)


class PSChangeTypeArrayAdd(PSChangeTypeArray):
    TYPE = "array-add"

    def __init__(self, domain, key, byhost, value):
        super().__init__(domain, byhost, key, value)
        self.value = self._generate_value_string(value)

    def _generate_value_string(self, value):
        values = []
        for v in value:
            values.append(self.to_xmlfrag(v))
        return values


class PSChangeTypeData(PSChangeTypeString):
    def __init__(self, domain, byhost, key, value):
        raise PSChangeTypeNotImplementedException(
            "%s not implemented" % self.__class__.__name__)


class PSChangeTypeDate(PSChangeTypeString):
    def __init__(self, domain, byhost, key, value):
        raise PSChangeTypeNotImplementedException(
            "%s not implemented" % self.__class__.__name__)
