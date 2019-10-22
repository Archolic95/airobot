"""
This file contains 2 classes:
    - ParseUtils containing utilies to parse data from UR robot
    - SecondaryMonitor, a class opening a socket to the robot and with methods
        to access data and send programs to the robot
Both use data from the secondary port of the URRobot.
Only the last connected socket on 3001 is the primary client !!!!
So do not rely on it unless you know no other client is running
(Hint the UR java interface is a client...)
http://support.universal-robots.com/Technical/PrimaryAndSecondaryClientInterface
"""

import logging
import socket
import struct
import time
from copy import copy
from threading import Thread, Condition, Lock

"""
Code built off of ursecmon.py in python-urx library
(https://github.com/SintefManufacturing/python-urx/blob/master/urx/ursecmon.py)
written by Oliver Roulet-Dubonnet (see original author info below)
__author__ = "Olivier Roulet-Dubonnet"
__copyright__ = "Copyright 2011-2013, Sintef Raufoss Manufacturing"
__credits__ = ["Olivier Roulet-Dubonnet"]
__license__ = "LGPLv3"
"""


class ParsingException(Exception):

    def __init__(self, *args):
        Exception.__init__(self, *args)


class Program(object):
    def __init__(self, prog):
        self.program = prog
        self.condition = Condition()

    def __str__(self):
        return "Program({})".format(self.program)

    __repr__ = __str__


class TimeoutException(Exception):

    def __init__(self, *args):
        Exception.__init__(self, *args)


class ParserUtils(object):
    """
    Class of parsing utilities for getting data from the UR robot
    through the TCP socket
    """

    def __init__(self):
        """
        Constructor for parsing utilities class
        """
        self.logger = logging.getLogger("monitor")
        self.version = (0, 0)
        self.packet_type_map = {
            "RobotModeData": 0,
            "JointData": 1,
            "CartesianInfo:": 4,
            "MasterBoardData": 3,
            "ToolData": 2,
            "AdditionalInfo": 8,
            "ForceModeData": 7,
        }

    def parse(self, data):
        """
        Parse a packet from the UR socket and return dictionary with the data.
        Data is parsed in sections by starting at the beginning and using the
        header information to unpack the data in chunks. struct format strings
        are used for unpacking based on the expected data being unpacked (see
        Python struct package for information on these format strings)

        Args:
            data (str? bytes?): Packet data in raw form

        Return:
            dict: Dictionary with interpretable state data from robot, with
                keys corresponding to different types of data

        """
        allData = {}
        while data:
            # Each iteration, pdata gets all the data from the next section in
            # the packet and this is the data that goes into the dictionary,
            # the rest of the packet gets put in data, which is parsed on the
            # next iteration this continues until the data variable is empty
            psize, ptype, pdata, data = self.analyze_header(data)

            # assert(ptype == 16), on first time?
            # print "We got packet with size %i and type %i" % (psize, ptype)
            if ptype == 16:
                allData["SecondaryClientData"] = \
                    self._get_data(pdata, "!iB", ("size", "type"))
                # First group tells about the whole packet, so put whole packet
                # back together for next iteration and send back to the parser
                data = (pdata + data)[5:]

            elif ptype == 0:
                # this parses RobotModeData for versions >=3.0 (i.e. 3.0)
                if psize == 38:
                    self.version = (3, 0)
                    allData['RobotModeData'] = \
                        self._get_data(
                            pdata, "!IBQ???????BBdd",
                            ("size", "type", "timestamp", "isRobotConnected",
                             "isRealRobotEnabled", "isPowerOnRobot",
                             "isEmergencyStopped", "isSecurityStopped",
                             "isProgramRunning", "isProgramPaused",
                             "robotMode", "controlMode", "speedFraction",
                             "speedScaling"))

                elif psize == 46:  # It's 46 bytes in 3.2
                    self.version = (3, 2)
                    allData['RobotModeData'] = \
                        self._get_data(
                            pdata, "!IBQ???????BBdd",
                            ("size", "type", "timestamp", "isRobotConnected",
                             "isRealRobotEnabled", "isPowerOnRobot",
                             "isEmergencyStopped", "isSecurityStopped",
                             "isProgramRunning", "isProgramPaused",
                             "robotMode", "controlMode", "speedFraction",
                             "speedScaling", "speedFractionLimit"))

                elif psize == 47:
                    self.version = (3, 5)
                    allData['RobotModeData'] = \
                        self._get_data(
                            pdata, "!IBQ???????BBddc",
                            ("size", "type", "timestamp", "isRobotConnected",
                             "isRealRobotEnabled", "isPowerOnRobot",
                             "isEmergencyStopped", "isSecurityStopped",
                             "isProgramRunning", "isProgramPaused",
                             "robotMode", "controlMode", "speedFraction",
                             "speedScaling", "speedFractionLimit",
                             "reservedByUR"))
                else:
                    allData["RobotModeData"] = \
                        self._get_data(
                            pdata, "!iBQ???????Bd",
                            ("size", "type", "timestamp", "isRobotConnected",
                             "isRealRobotEnabled", "isPowerOnRobot",
                             "isEmergencyStopped", "isSecurityStopped",
                             "isProgramRunning", "isProgramPaused",
                             "robotMode", "speedFraction"))

            elif ptype == 1:
                tmpstr = ["size", "type"]
                for i in range(0, 6):
                    tmpstr += ["q_actual%s" % i, "q_target%s" % i,
                               "qd_actual%s" % i, "I_actual%s" % i,
                               "V_actual%s" % i, "T_motor%s" % i,
                               "T_micro%s" % i, "jointMode%s" % i]

                allData["JointData"] = \
                    self._get_data(
                        pdata, "!iB dddffffB dddffffB dddffffB"
                               "dddffffB dddffffB dddffffB", tmpstr)

            elif ptype == 4:
                if self.version < (3, 2):
                    allData["CartesianInfo"] = \
                        self._get_data(
                            pdata, "iBdddddd",
                            ("size", "type", "X", "Y", "Z", "Rx", "Ry", "Rz"))
                else:
                    allData["CartesianInfo"] = \
                        self._get_data(
                            pdata, "iBdddddddddddd",
                            ("size", "type", "X", "Y", "Z", "Rx", "Ry", "Rz",
                             "tcpOffsetX", "tcpOffsetY", "tcpOffsetZ",
                             "tcpOffsetRx", "tcpOffsetRy", "tcpOffsetRz"))

            elif ptype == 5:
                allData["LaserPointer(OBSOLETE)"] = \
                    self._get_data(
                        pdata, "iBddd", ("size", "type"))

            elif ptype == 3:
                if self.version >= (3, 0):
                    fmt = "iBiibbddbbddffffBBb"  # firmware >= 3.0
                else:
                    fmt = "iBhhbbddbbddffffBBb"  # firmware < 3.0

                allData["MasterBoardData"] = \
                    self._get_data(
                        pdata, fmt,
                        ("size", "type", "digitalInputBits",
                         "digitalOutputBits", "analogInputRange0",
                         "analogInputRange1", "analogInput0", "analogInput1",
                         "analogInputDomain0", "analogInputDomain1",
                         "analogOutput0", "analogOutput1",
                         "masterBoardTemperature", "robotVoltage48V",
                         "robotCurrent", "masterIOCurrent"))

            elif ptype == 2:
                allData["ToolData"] = \
                    self._get_data(
                        pdata, "iBbbddfBffB",
                        ("size", "type", "analoginputRange2",
                         "analoginputRange3", "analogInput2", "analogInput3",
                         "toolVoltage48V", "toolOutputVoltage", "toolCurrent",
                         "toolTemperature", "toolMode"))

            elif ptype == 9:
                # This package has a length of 53 bytes. It is used internally
                # by Universal Robots software only and should be skipped.
                continue

            elif ptype == 8 and self.version >= (3, 2):
                allData["AdditionalInfo"] = \
                    self._get_data(
                        pdata, "iB??",
                        ("size", "type", "teachButtonPressed",
                         "teachButtonEnabled"))

            elif ptype == 7 and self.version >= (3, 2):
                allData["ForceModeData"] = \
                    self._get_data(
                        pdata, "iBddddddd",
                        ("size", "type", "x", "y", "z", "rx", "ry", "rz",
                         "robotDexterity"))

            elif ptype == 20:
                tmp = self._get_data(
                    pdata, "!iB Qbb",
                    ("size", "type", "timestamp",
                     "source", "robotMessageType"))

                if tmp["robotMessageType"] == 3:
                    allData["VersionMessage"] = \
                        self._get_data(
                            pdata, "!iBQbb bAbBBiAb",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "projectNameSize",
                             "projectName", "majorVersion", "minorVersion",
                             "svnRevision", "buildDate"))

                elif tmp["robotMessageType"] == 6:
                    allData["robotCommMessage"] = \
                        self._get_data(
                            pdata, "!iBQbb iiAc",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "code", "argument",
                             "messageText"))

                elif tmp["robotMessageType"] == 1:
                    allData["labelMessage"] = \
                        self._get_data(
                            pdata, "!iBQbb iAc",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "id", "messageText"))

                elif tmp["robotMessageType"] == 2:
                    allData["popupMessage"] = \
                        self._get_data(
                            pdata, "!iBQbb ??BAcAc",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "warning", "error",
                             "titleSize", "messageTitle", "messageText"))

                elif tmp["robotMessageType"] == 0:
                    allData["messageText"] = \
                        self._get_data(
                            pdata, "!iBQbb Ac",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "messageText"))

                elif tmp["robotMessageType"] == 8:
                    allData["varMessage"] = \
                        self._get_data(
                            pdata, "!iBQbb iiBAcAc",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "code", "argument",
                             "titleSize", "messageTitle", "messageText"))

                elif tmp["robotMessageType"] == 7:
                    allData["keyMessage"] = \
                        self._get_data(
                            pdata, "!iBQbb iiBAcAc",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "code", "argument",
                             "titleSize", "messageTitle", "messageText"))

                elif tmp["robotMessageType"] == 5:
                    allData["keyMessage"] = \
                        self._get_data(
                            pdata, "!iBQbb iiAc",
                            ("size", "type", "timestamp", "source",
                             "robotMessageType", "code", "argument",
                             "messageText"))
                else:
                    self.logger.debug("Message type parser "
                                      "not implemented %s", tmp)
            else:
                self.logger.debug("Unknown packet type %s with "
                                  "size %s", ptype, psize)

        return allData

    def _get_data(self, data, struct_fmt, key_names):
        """
        Method to fill packet data into a dictionary

        Args:
            data (bytes): Data from robot packet
            struct_fmt (str): struct format, but with added A for arrays and no
                support for numerical in fmt
            key_names (tuple of str): Strings used to store values in data
                dictionary with meaningful descriptions

        Return:
            dict: Dictionary with keys from names arg, and values filled
                in by unpacking the data with struct
        """
        tmpdata = copy(data)
        fmt = struct_fmt.strip()  # remove whitespace
        d = dict()
        i = 0
        j = 0
        while j < len(fmt) and i < len(key_names):
            f = fmt[j]
            if f in (" ", "!", ">", "<"):
                j += 1
            elif f == "A":  # we got an array
                # first we need to find its size
                if j == len(fmt) - 2:
                    # we are last element, size is the rest of data in packet
                    arraysize = len(tmpdata)
                else:
                    # size should be given in last element
                    asn = key_names[i - 1]
                    if not asn.endswith("Size"):
                        raise ParsingException("Error, array without size ! "
                                               "%s %s" % (asn, i))
                    else:
                        arraysize = d[asn]
                d[key_names[i]] = tmpdata[0:arraysize]
                # print "Array is ", names[i], d[names[i]]
                tmpdata = tmpdata[arraysize:]
                j += 2
                i += 1
            else:
                # use struct library to tell us how many bytes we are
                # unpacking
                fmtsize = struct.calcsize(fmt[j])
                if len(tmpdata) < fmtsize:  # seems to happen on windows
                    raise ParsingException("Error, length of data smaller "
                                           "than advertized: ", len(tmpdata),
                                           fmtsize, "for names ", key_names, f,
                                           i, j)
                # unpack the data based on the format and size
                d[key_names[i]] = struct.unpack("!" + f, tmpdata[0:fmtsize])[0]

                # keep moving through the data
                tmpdata = tmpdata[fmtsize:]
                j += 1
                i += 1
        return d

    def get_header(self, data):
        """
        Looks at raw data sent from robot and unpacks it's header
        (first 5 bytes)

        Arguments:
            data (binary? bytes?) -- Raw data received from socket connection
                 to robot

        Returns:
            int: Size of the packet, in bytes
            int: Indication of the type of packet we got
        """
        # !: network (=big-endian) byte order
        # iB: integer (4 bytes) followed by unsigned char (1 byte)
        packet_size, packet_type = struct.unpack("!iB", data[0:5])
        return packet_size, packet_type

    def analyze_header(self, data):
        """
        Analyzes header returned from get_header method,
        so that parser knows how much data we got and
        what type it is?

        Arguments:
            data (str): Raw data received from socket connection

        Raises:
            ParsingException: Packet size too small to read header
            ParsingException: Declared packet size is smaller than
                own header size
            ParsingException: Declared packet size from header is
                smaller than measured number of bytes in the packet

        Returns:
            int: Number of bytes in the packet
            int: Indication of the type of packet
            bytes: Sliced packet, only taking how many bytes the
                header says we received
            bytes: The rest of the packet, data that will be
                unpacked on the next parser iteration
        """
        if len(data) < 5:
            raise ParsingException("Packet size %s smaller than header size"
                                   "(5 bytes)" % len(data))
        else:
            # unpack header data into packet size and packet type
            # in the form (integer, unsigned char)
            packet_size, packet_type = self.get_header(data)
            if packet_size < 5:
                raise ParsingException("Error, declared length of data smaller"
                                       "than its own header(5): ", packet_size)
            elif packet_size > len(data):
                raise ParsingException("Error, length of data smaller "
                                       "(%s) than declared (%s)"
                                       % (len(data), packet_size))
        return packet_size, packet_type, data[:packet_size], data[packet_size:]

    def find_first_packet(self, data):
        """
        find the first complete packet in a string by checking for length
        matching expected length, returns None if none found
        """
        """
        Finds the first complete packet in a string, and returns
        None if none found

        Args:
            data (bytes): Incoming data from socket stream

        Returns:
            [type] -- [description]
        """
        counter = 0
        limit = 10
        while True:
            # check if we at least have enough to read header
            if len(data) >= 5:
                psize, ptype = self.get_header(data)

                # packet size should not be less than 5 bytes,
                # packet size should not be too large
                # initial packet type should be 16
                if psize < 5 or psize > 2000 or ptype != 16:
                    # move one byte at a time along datastream
                    data = data[1:]
                    counter += 1
                    if counter > limit:
                        self.logger.warning(
                            "tried %s times to find a packet in data,"
                            "advertised packet size: %s, type: %s", counter,
                            psize, ptype)
                        self.logger.warning("Data length: %s", len(data))
                        limit = limit * 10
                elif len(data) >= psize:
                    self.logger.debug("Got packet with size %s and type %s",
                                      psize, ptype)
                    if counter:
                        self.logger.info("Remove %s bytes of garbage at"
                                         "begining of packet", counter)
                    # We have something which looks like a packet"
                    return (data[:psize], data[psize:])
                else:
                    # packet is not complete
                    self.logger.debug("Packet is not complete, advertised size"
                                      "is %s, received size is %s, type is %s",
                                      psize, len(data), ptype)
                    return None
            else:
                return None


class SecondaryMonitor(Thread):
    """
    Class to monitor data from secondary port and send programs to robot
    """

    def __init__(self, host):
        """
        Constructor for UR monitor class

        Arguments:
            host (str): Robot's IP address
        """
        Thread.__init__(self)
        self.logger = logging.getLogger("monitor")
        self._parser = ParserUtils()
        self._dict = {}
        self._dictLock = Lock()
        self.host = host
        secondary_port = 30002  # Secondary client interface on UR
        self._socket = socket.create_connection((self.host,
                                                 secondary_port),
                                                timeout=1)
        self._prog_queue = []
        self._prog_queue_lock = Lock()
        self._dataqueue = bytes()
        self._trystop = False  # to stop thread
        self.running = False  # True when robot is on and listening
        self._dataEvent = Condition()
        self.lastpacket_timestamp = 0

        self.start()
        self.wait()  # make sure we got some data before someone calls us

    def __del__(self):
        self.close()

    def send_program(self, prog):
        """
        Send program to robot in URRobot format. If another program
        is sent while a program is running, the first program is aborded.

        Args:
            prog (str): URScript style program or command to run
        """
        prog.strip()
        self.logger.debug("Enqueueing program: %s", prog)
        if not isinstance(prog, bytes):
            prog = prog.encode()  # make sure we're sending raw data

        data = Program(prog + b"\n")
        with data.condition:
            with self._prog_queue_lock:
                self._prog_queue.append(data)
            data.condition.wait()
            self.logger.debug("program send: %s", data)

    def run(self):
        """
        Check program execution status in the secondary client data packet we
        get from the robot. This interface uses only data from the secondary
        client interface (see UR doc), only the last connected client is
        the primary client, so this is not guaranted and we cannot rely on
        information to the primary client.

        This is the method that is started in a separate thread when the
        monitor is instantiated.

        The flow is to pop a program off the program queue, send the data,
        receive the incoming data, parse it, check to make sure the robot is
        okay, and then let everyone know we got some new data
        """
        while not self._trystop:
            with self._prog_queue_lock:
                if len(self._prog_queue) > 0:
                    # get data from the queue and send it out
                    data = self._prog_queue.pop(0)
                    self._socket.send(data.program)
                    with data.condition:
                        data.condition.notify_all()

            data = self._get_data()
            try:
                tmpdict = self._parser.parse(data)
                with self._dictLock:
                    self._dict = tmpdict
            except ParsingException as ex:
                self.logger.warning("Error parsing one packet "
                                    "from urrobot: %s", ex)
                continue

            if "RobotModeData" not in self._dict:
                self.logger.warning("Got a packet from robot without "
                                    "RobotModeData, strange ...")
                continue

            self.lastpacket_timestamp = time.time()

            rmode = 0
            if self._parser.version >= (3, 0):
                rmode = 7

            if (self.check_if_running(rmode)):
                self.running = True
            else:
                if self.running:
                    self.logger.error("Robot not running: " +
                                      str(self._dict["RobotModeData"]))
                self.running = False
            with self._dataEvent:
                # print("X: new data")
                self._dataEvent.notifyAll()

    def check_if_running(self, rmode):
        """
        Method to check if all the data we received from the latest packet
        tells us that everything is fine with the robot. If not, will
        return False and the main monitor thread will send out an error

        Arguments:
            rmode (int): Varible indicating robotMode value that is sent
                to the monitor from the robot. Used for one of the checks
                for if the robot is running or not

        Returns:
            bool: True if robot seems to be running, False if not
        """
        status = True

        status = (status and
                  (self._dict["RobotModeData"]["robotMode"] == rmode))
        status = (status and
                  (self._dict["RobotModeData"]["isRealRobotEnabled"] is True))
        status = (status and
                  (self._dict["RobotModeData"]["isEmergencyStopped"] is False))
        status = (status and
                  (self._dict["RobotModeData"]["isSecurityStopped"] is False))
        status = (status and
                  (self._dict["RobotModeData"]["isRobotConnected"] is True))
        status = (status and
                  (self._dict["RobotModeData"]["isPowerOnRobot"] is True))

        if status is True:
            return True
        else:
            return False

    def _get_data(self):
        """
        Returns something that looks like a packet (nothing is guaranted)
        """
        while True:
            ans = self._parser.find_first_packet(self._dataqueue[:])
            if ans:
                # Reset the queue with everything passed the packet we got
                self._dataqueue = ans[1]

                # Return the packet we found
                return ans[0]
            else:
                # Keep putting more data into the queue if we haven't found
                # a packet yet
                tmp = self._socket.recv(1024)
                self._dataqueue += tmp

    def wait(self, timeout=1.0):
        """
        Wait for next data packet from robot
        """
        tstamp = self.lastpacket_timestamp
        with self._dataEvent:
            self._dataEvent.wait(timeout)
            if tstamp == self.lastpacket_timestamp:
                raise TimeoutException("Did not receive a valid data packet "
                                       "from robot in %f" % timeout)

    def get_cartesian_info(self, wait=False):
        """
        Get end effector information from robot

        Keyword Arguments:
            wait (bool): Boolean to tell whether to block
                until data is returned (default: {False})

        Returns:
            dict: Value in the state dict with the CartesianInfo data
        """
        if wait:
            self.wait()
        with self._dictLock:
            if "CartesianInfo" in self._dict:
                return self._dict["CartesianInfo"]
            else:
                return None

    def get_all_data(self, wait=False):
        """
        Return all most recent data in big dictionary

        Keyword Arguments:
            wait (bool): Boolean to tell whether to block
                until data is returned (default: {False})

        Returns:
            dict: Full state dict
        """
        if wait:
            self.wait()
        with self._dictLock:
            return self._dict.copy()

    def get_joint_data(self, wait=False):
        if wait:
            self.wait()
        with self._dictLock:
            if "JointData" in self._dict:
                return self._dict["JointData"]
            else:
                return None

    def get_digital_out(self, nb, wait=False):
        if wait:
            self.wait()
        with self._dictLock:
            output = self._dict["MasterBoardData"]["digitalOutputBits"]
        mask = 1 << nb
        if output & mask:
            return 1
        else:
            return 0

    def get_digital_out_bits(self, wait=False):
        if wait:
            self.wait()
        with self._dictLock:
            return self._dict["MasterBoardData"]["digitalOutputBits"]

    def get_digital_in(self, nb, wait=False):
        if wait:
            self.wait()
        with self._dictLock:
            output = self._dict["MasterBoardData"]["digitalInputBits"]
        mask = 1 << nb
        if output & mask:
            return 1
        else:
            return 0

    def get_digital_in_bits(self, wait=False):
        if wait:
            self.wait()
        with self._dictLock:
            return self._dict["MasterBoardData"]["digitalInputBits"]

    def get_analog_in(self, nb, wait=False):
        if wait:
            self.wait()
        with self._dictLock:
            return self._dict["MasterBoardData"]["analogInput" + str(nb)]

    def get_analog_inputs(self, wait=False):
        if wait:
            self.wait()
        with self._dictLock:
            return (self._dict["MasterBoardData"]["analogInput0"],
                    self._dict["MasterBoardData"]["analogInput1"])

    def is_program_running(self, wait=False):
        """
        return True if robot is executing a program
        Rmq: The refresh rate is only 10Hz so the information may be outdated
        """
        if wait:
            self.wait()
        with self._dictLock:
            return self._dict["RobotModeData"]["isProgramRunning"]

    def close(self):
        self._trystop = True
        self.join()

        if self._socket:
            with self._prog_queue_lock:
                self._socket.close()


class RealtimeMonitor(Thread):
    def __init__(self, host):
        Thread.__init__(self)
        self.logger = logging.getLogger('realtime_monitor')
        self.daemon = True
        self._stop_event = True
        self._data_event = Condition()
        self._data_lock = Lock()
        self._rt_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._rt_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        
        self.host = host

        self._tstamp = None
        self._ctrl_tstamp = None
        self.q_actual = None
        self.q_target = None
        self.tcp = None
        self.tcp_force = None
        self._recv_time = 0
        self._last_ctrl_tstamp = 0

        self._buffering = False
        self._buffer_lock = Lock()
        self._buffer = []
        self._csys = None
        self._csys_lock = Lock()

        self.rt_strcut692 = struct.Struct(
            '>'
        )
        self.rt_struct540 = struct.Struct(
            '>'
        )

    def set_csys(self, csys):
        with self._csys_lock:
            self._csys = csys

    def _recv_bytes(self, n_bytes):
        recv_time = 0
        pkg = b''
        while len(pkg) < n_bytes:
            pkg += self._rt_socket.recv(n_bytes - len(pkg))
            if recv_time == 0:
                recv_time = time.time()
        self._recv_time = recv_time 
        return pkg

    def wait(self):
        with self._data_event:
            self._data_event.wait()

    def _recv_rt_data(self):
        header = self._recv_bytes(4)

        tstamp = self._recv_time
        pkgsize = struct.unpack('>i', header)[0]
        self.logger.debug(
            'Received header telling us this package is %s bytes long',
            pkgsize
        )
        payload = self._recv_bytes(pkgsizeu - 4)
        if pkgsize >= 692:
            unp = self.rt_strcut692.unpack(payload[:self.rt_strcut692.size])
        elif pkgsize >= 540:
            unp = self.rt_struct540.unpack(payload[:self.rt_struct540.size])
        else:
            self.logger.warning(
                'Error: received packet of length smaller than 540: %s',
                pkgsize)
            )
        
        with self._data_lock:
            self._tstamp = tstamp
            self._ctrl_tstamp = np.array(unp[0])
            if self._last_ctrl_tstamp != 0 and (
                    self._ctrl_tstamp - self._last_ctrl_tstamp) > 0.01:
                self.logger.warning(
                    'Error: the controller failed to send us a packet'
                    ' time since last packet: %s s',
                    self._ctrl_tstamp - self._last_ctrl_tstamp 
                )
            self._last_ctrl_tstamp = self._ctrl_tstamp

        if self._buffering:
            with self._buffer_lock:
                self._buffer.append(
                    (self._tstamp,
                    self._ctrl_tstamp))

        with self._data_event:
            self._data_event.notifyAll()

    def start_buffering(self):
        self._buffer = []
        self._buffering = True

    def stop_buffering(self):
        self._buffering = False

    def try_pop_buffer(self):
        with self._buffer_lock:
            if len(self._buffer) > 0:
                return self._buffer.pop(0)
            else:
                return None

    def pop_buffer(self):
        while True:
            with self._buffer_lock:
                if len(self._buffer) > 0:
                    return self._buffer.pop(0)
            time.sleep(0.001)

    def get_buffer(self):
        with self._buffer_lock:
            return deepcopy(self._buffer)

    def get_all_data(self, wait=True):
        if wait:
            self.wait()
        with self._data_lock:
            # TODO get dict of everything

    def stop(self):
        self._stop_event = True

    def close(self):
        self.stop()
        self.joint()

    def run(self):
        self._stop_event = False
        self._rt_socket.connect((self.host, 30003))
        while not self._stop_event:
            self._recv_rt_data()
        self._rt_socket.close()
