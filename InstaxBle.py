#!/usr/bin/env python3

from math import ceil
from struct import pack, unpack_from
from time import sleep
from Types import EventType
import argparse
import LedPatterns
import simplepyble
import sys


class InstaxBle:
    def __init__(self,
                 device_address=None,
                 print_enabled=False,
                 use_printer=True,
                 verbose=False,
                 quiet=False):
        """
        Initialize the InstaxBle class.
        deviceAddress: if specified, will only connect to a printer with this address.
        printEnabled: by default, actual printing is disabled to prevent misprints.
        """
        self.printEnabled = print_enabled
        self.peripheral = None
        self.deviceAddress = device_address
        self.usePrinter = use_printer
        self.quiet = quiet
        self.verbose = verbose if not self.quiet else False
        self.serviceUUID = '70954782-2d83-473d-9e5f-81e1d02d5273'
        self.writeCharUUID = '70954783-2d83-473d-9e5f-81e1d02d5273'
        self.notifyCharUUID = '70954784-2d83-473d-9e5f-81e1d02d5273'
        self.packetsForPrinting = []
        self.waitingForResponse = False

        adapters = simplepyble.Adapter.get_adapters()
        if len(adapters) == 0:
            if not self.quiet:
                sys.exit("No bluetooth adapters found (are they enabled?)")
            else:
                sys.exit()
        if len(adapters) > 1 and self.verbose:
            print(f"Found multiple adapters: {', '.join([adapter.identifier() for adapter in adapters])}")
            print(f"Using the first one: {adapters[0].identifier()}")
        self.adapter = adapters[0]

    def parse_response(self, packet):
        """ Parse the response packet and print the result """
        # todo: create parsers for the different types of responses
        return

    def notification_handler(self, packet):
        """ Gets called whenever the printer replies and handles parsing the received data """
        print('at handler')
        if self.verbose:
            print('Notification handler:')
            print(f'\t{self.prettify_bytearray(packet[:40])}')
        if not self.quiet:
            if len(packet) < 8:
                print(f"\tError: response packet size should be >= 8 (was {len(packet)})!")
            elif not self.validate_checksum(packet):
                print("\tResponse packet checksum was invalid!")

        header, length, op1, op2 = unpack_from('<HHBB', packet)
        print('\theader: ', header, '\t', self.prettify_bytearray(packet[0:2]))
        print('\tlength: ', length, '\t', self.prettify_bytearray(packet[2:4]))
        print('\top1: ', op1, '\t\t', self.prettify_bytearray(packet[4:5]))
        print('\top2: ', op2, '\t\t', self.prettify_bytearray(packet[5:6]))

        if self.verbose:
            try:
                event = EventType((op1, op2))
            except Exception:
                event = f"Unknown event: ({op1}, {op2})"
            print('\tevent: ', event)

        self.parse_response(packet)
        # self.waitingForResponse = False

        print("packets to go: ", len(self.packetsForPrinting))
        if len(self.packetsForPrinting) > 0:
            packet = self.packetsForPrinting.pop(0)
            self.send_packet(packet)

    def connect(self, timeout=0):
        """ Connect to the printer. Stops trying after <timeout> seconds. """
        if not self.usePrinter:
            return

        self.peripheral = self.find_device(timeout=timeout)
        if self.peripheral:
            try:
                if self.verbose:
                    print(f"\n\nConnecting to: {self.peripheral.identifier()} [{self.peripheral.address()}]")
                self.peripheral.connect()
            except Exception as e:
                if not self.quiet:
                    print('error on connecting: ', e)

            if self.peripheral.is_connected():
                if self.verbose:
                    print(f"Connected (mtu: {self.peripheral.mtu()})")
                    print('Attaching notification_handler')
                try:
                    self.peripheral.notify(self.serviceUUID, self.notifyCharUUID, self.notification_handler)
                except Exception as e:
                    if not self.quiet:
                        print('error on attaching notification_handler: ', e)

    def disconnect(self):
        """ Disconnect from the printer (if connected) """
        print('at disconnect')
        if not self.usePrinter:
            return
        if self.peripheral:
            print('start disconnect')
            if self.peripheral.is_connected():
                self.peripheral.disconnect()
            print('disconnect done')

    def enable_printing(self):
        """ Enable printing. """
        self.printEnabled = True

    def disable_printing(self):
        """ Disable printing. """
        self.printEnabled = False

    def find_device(self, timeout=0):
        """" Scan for our device and return it when found """
        if self.verbose:
            print('Looking for instax printer...')
        secondsTried = 0
        while True:
            self.adapter.scan_for(2000)
            peripherals = self.adapter.scan_get_results()
            for peripheral in peripherals:
                foundName = peripheral.identifier()
                foundAddress = peripheral.address()
                if self.verbose:
                    print(f"Found: {foundName} [{foundAddress}]")
                if (self.deviceAddress is None and foundName.startswith('INSTAX-')) or \
                   foundAddress == self.deviceAddress:
                    if foundAddress.startswith('FA:AB:BC'):  # start of IOS endpooint
                        # to convert to ANDROID endpoint, replace 'FA:AB:BC' to '88:B4:36')
                        if peripheral.is_connectable():
                            return peripheral
                        elif not self.quiet:
                            print(f"Printer at {foundAddress} is not connectable")
            secondsTried += 1
            if timeout != 0 and secondsTried >= timeout:
                return None

    def create_color_payload(self, colorArray, speed, repeat, when):
        """
        Create a payload for a color pattern.
        colorArray: array of RGB values to use in animation, e.g. [[255, 0, 0], [0, 255, 0], [0, 0, 255]]
        speed: time per frame/color: higher is slower animation
        repeat: 0 = don't repeat (so play once), 1-254 = times to repeat, 255 = repeat forever
        when: 0 = normal, 1 = on print, 2 = on print completion, 3 = pattern switch
        """
        payload = pack('BBBB', when, len(colorArray), speed, repeat)
        for color in colorArray:
            payload += pack('BBB', color[0], color[1], color[2])
        return payload

    def send_led_pattern(self, pattern, speed=5, repeat=255, when=0):
        """ Send a LED pattern to the Instax printer. """
        payload = self.create_color_payload(pattern, speed, repeat, when)
        packet = self.create_packet(EventType.LED_PATTERN_SETTINGS, payload)
        self.send_packet(packet)

    def prettify_bytearray(self, value):
        """ Helper funtion to convert a bytearray to a string of hex values. """
        return ' '.join([f'{x:02x}' for x in value])

    def create_checksum(self, bytearray):
        """ Create a checksum for a given packet. """
        return (255 - (sum(bytearray) & 255)) & 255

    def create_packet(self, eventType, payload=b''):
        """ Create a packet to send to the printer. """
        if isinstance(eventType, EventType):  # allows passing in an event or a value directly
            eventType = eventType.value

        header = b'\x41\x62'  # 'Ab' means from client to printer, responses from printer start with 'aB'
        opCode = bytes([eventType[0], eventType[1]])
        packetSize = pack('>H', 7 + len(payload))
        packet = header + packetSize + opCode + payload
        packet += pack('B', self.create_checksum(packet))
        return packet

    def validate_checksum(self, packet):
        """ Validate the checksum of a packet. """
        return (sum(packet) & 255) == 255

    def send_packet(self, packet):
        """ Send a packet to the printer """
        if self.usePrinter and not self.quiet:
            if not self.peripheral:
                print("no peripheral to send packet to")
            elif not self.peripheral.is_connected():
                print("peripheral not connected")

        header, length, op1, op2 = unpack_from('<HHBB', packet)
        try:
            event = EventType((op1, op2))
        except Exception:
            event = 'Unknown event'

        if self.verbose:
            print('sending eventtype: ', event)

        # print('sending: ', type(packet), packet[0:40])
        if event != EventType.PRINT_IMAGE_DOWNLOAD_DATA:
            if self.usePrinter:
                print('send non image_download_data')
                self.peripheral.write_command(self.serviceUUID, self.writeCharUUID, packet)
        else:  # PRINT_IMAGE_DOWNLOAD_DATA
            smallPacketSize = 182
            numberOfParts = ceil(len(packet) / smallPacketSize)
            print("number of packets to send: ", numberOfParts)
            for subPartIndex in range(numberOfParts):
                print((subPartIndex + 1), '/', numberOfParts)
                subPacket = packet[subPartIndex * smallPacketSize:subPartIndex * smallPacketSize + smallPacketSize]

                if self.usePrinter:
                    print(subPacket)
                    self.peripheral.write_command(self.serviceUUID, self.writeCharUUID, subPacket)
            # print('send image data: ', end='')
            # for iteration in range(ceil(len(packet)/smallPacketSize)):
            #     subPacket = packet[iteration * smallPacketSize:iteration * smallPacketSize + smallPacketSize]
            #     if self.usePrinter:
            #         print('.', end='')
            #         self.peripheral.write_command(self.serviceUUID, self.writeCharUUID, subPacket)
            #         # sleep(0.1)
            # subPacket = packet[smallPacketSize * 9:]
            # if self.usePrinter:
            #     self.peripheral.write_command(self.serviceUUID, self.writeCharUUID, subPacket)
            print('done')

    # TODO: printer doesn't seem to respond to this?
    # async def shut_down(self):
    #     """ Shut down the printer. """
    #     packet = self.create_packet(EventType.SHUT_DOWN)
    #     return self.send_packet(packet)

    def image_to_bytes(self, imagePath):
        """ Convert an image to a bytearray """
        imgdata = None
        try:
            # TODO: I think returning image.read() already returns bytes so no need for bytearray?
            with open(imagePath, "rb") as image:
                imgdata = bytearray(image.read())
            return imgdata
        except Exception as e:
            if not self.quiet:
                print('Error loading image: ', e)

    def print_image(self, imgSrc):
        """
        print an image. Either pass a path to an image (as a string) or pass
        the bytearray to print directly
        """
        imgData = imgSrc
        if isinstance(imgSrc, str):  # if it's a path, load the image contents
            imgData = self.image_to_bytes(imgSrc)

        self.packetsForPrinting = [
            self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_START, b'\x02\x00\x00\x00\x00\x00' + pack('>H', len(imgData)))
        ]

        # divide image data up into chunks of <chunkSize> bytes and pad the last chunk with zeroes if needed
        # chunkSize = 900
        chunkSize = 900
        imgDataChunks = [imgData[i:i + chunkSize] for i in range(0, len(imgData), chunkSize)]
        if len(imgDataChunks[-1]) < chunkSize:
            imgDataChunks[-1] = imgDataChunks[-1] + bytes(chunkSize - len(imgDataChunks[-1]))

        # create a packet from each of our chunks, this includes adding the chunk number
        for index, chunk in enumerate(imgDataChunks):
            imgDataChunks[index] = pack('>I', index) + chunk  # add chunk number as int (4 bytes)
            self.packetsForPrinting.append(self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_DATA, imgDataChunks[index]))

        self.packetsForPrinting.append(self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_END))

        if self.printEnabled:
            self.packetsForPrinting.append(self.create_packet(EventType.PRINT_IMAGE))
            self.packetsForPrinting.append(self.create_packet((0, 2), b'\x02'))
        elif not self.quiet:
            print("Printing is disabled, sending all packets except the actual print command")

        # for packet in self.packetsForPrinting:
        #     print(self.prettify_bytearray(packet))
        # exit()
        # send the first packet from our list, the packet handler will take care of the rest

        if self.usePrinter:
            packet = self.packetsForPrinting.pop(0)
            self.send_packet(packet)
            print("entering wait loop")
            try:
                while len(self.packetsForPrinting) > 0:
                    sleep(0.1)
            except KeyboardInterrupt:
                raise KeyboardInterrupt


    def print_services(self):
        """ Get and display and overview of the printer's services and characteristics """
        if self.verbose:
            print("Successfully connected, listing services...")
        services = self.peripheral.services()
        service_characteristic_pair = []
        for service in services:
            for characteristic in service.characteristics():
                service_characteristic_pair.append((service.uuid(), characteristic.uuid()))

        for i, (service_uuid, characteristic) in enumerate(service_characteristic_pair):
            print(f"{i}: {service_uuid} {characteristic}")

    def get_function_info(self):
        """ Get and display the printer's function info """
        if self.verbose:
            print("Getting function info...")
        packet = self.create_packet(EventType.SUPPORT_FUNCTION_INFO, b'0x02')
        self.send_packet(packet)


def main(args={}):
    """ Main script used as an example of how to control the printer """
    args['device_address'] = 'FA:AB:BC:4E:20:CE'
    instax = InstaxBle(**args)
    try:
        # instax.enable_printing()  # uncomment this line to enable actual printing
        instax.connect()
        # instax.get_function_info()
        # print("Successfully connected, listing services...")
        # instax.print_services()

        # instax.send_led_pattern(LedPatterns.blinkBlue)
        # while instax.waitingForResponse:
        #     print('waiting')
        # sleep(5)
        # instax.send_led_pattern(LedPatterns.pulseGreen)
        # while instax.waitingForResponse:
        #     sleep(5)
        # instax.send_led_pattern(LedPatterns.blinkRGB)
        # sleep(5)
        # while instax.waitingForResponse:
        #     sleep(5)
        # instax.print_services()
        # instax.print_image('moos.jpeg')
        instax.print_image('parsed_img.jpg')
        print('after print image, start paus')
        instax.disconnect()
    except Exception:
        instax.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--device-address')
    parser.add_argument('-p', '--print-enabled', action='store_true')
    parser.add_argument('-d', '--use-printer', action='store_false')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-q', '--quiet', action='store_true')
    args = parser.parse_args()

    main(vars(args))
    # signal.pause()
    # loop = asyncio.get_event_loop()
    # try:
    #     loop.run_forever()
    # finally:
    #     loop.close()
