////////////////////////////////////////////////////////////////
// Orkid Media Engine
// Copyright 1996-2023, Michael T. Mayers.
// Distributed under the MIT License.
// see license-mit.txt in the root of the repo, and/or https://opensource.org/license/mit/
////////////////////////////////////////////////////////////////

#include "pyext.h"
#include <pybind11/numpy.h>
#include <ork/lev2/aud/audiodevice.h>
#include <ork/lev2/aud/stream/audiodevice_stream.h>

///////////////////////////////////////////////////////////////////////////////
namespace ork::lev2 {
///////////////////////////////////////////////////////////////////////////////

///////////////////////////////////////////////////////////////////////////////
    void pyinit_aud_device(py::module& lev2_module) {
    /////////////////////////////////////////////////////////////////////////////////
    auto type_codec = python::pb11_typecodec_t::instance();
    /////////////////////////////////////////////////////////////////////////////////
    // AudioDeviceInfo
    /////////////////////////////////////////////////////////////////////////////////
    auto auddevinfo_t = py::class_<AudioDeviceInfo, audiodeviceinfo_ptr_t>(lev2_module, "AudioDeviceInfo")
        .def_readonly("name", &AudioDeviceInfo::_name)
        .def_readonly("input_short_id", &AudioDeviceInfo::_input_short_id)
        .def_readonly("output_short_id", &AudioDeviceInfo::_output_short_id)
        .def_readonly("max_input_channels", &AudioDeviceInfo::_max_input_channels)
        .def_readonly("max_output_channels", &AudioDeviceInfo::_max_output_channels)
        .def_readonly("sample_rate", &AudioDeviceInfo::_sample_rate)
        .def_readonly("device_index", &AudioDeviceInfo::_device_index)
        .def_property_readonly("supported_input_rates", [](audiodeviceinfo_ptr_t info) -> py::list {
          py::list result;
          for (auto r : info->_supported_input_rates) {
            result.append(r);
          }
          return result;
        })
        .def_property_readonly("supported_output_rates", [](audiodeviceinfo_ptr_t info) -> py::list {
          py::list result;
          for (auto r : info->_supported_output_rates) {
            result.append(r);
          }
          return result;
        })
        .def("__repr__", [](audiodeviceinfo_ptr_t info) -> std::string {
          return FormatString("AudioDeviceInfo(name='%s', in=%d, out=%d, sr=%g)",
                              info->_name.c_str(),
                              info->_max_input_channels,
                              info->_max_output_channels,
                              info->_sample_rate);
        });
    type_codec->registerStdCodec<audiodeviceinfo_ptr_t>(auddevinfo_t);
    /////////////////////////////////////////////////////////////////////////////////
    // enumerateAudioDevices
    /////////////////////////////////////////////////////////////////////////////////
    lev2_module.def("enumerateAudioDevices", []() -> py::list {
      auto devices = enumerateAudioDevices();
      py::list result;
      for (const auto& dev : devices) {
        result.append(dev);
      }
      return result;
    });
    lev2_module.def("findAudioDeviceByShortId", [](const std::string& short_id) -> audiodeviceinfo_ptr_t {
      return findAudioDeviceByShortId(short_id);
    }, py::arg("short_id"));
    /////////////////////////////////////////////////////////////////////////////////
    auto auddev_t = py::class_<AudioDevice, audiodevice_ptr_t>(lev2_module, "AudioDevice"); //
    type_codec->registerStdCodec<audiodevice_ptr_t>(auddev_t);
    /////////////////////////////////////////////////////////////////////////////////
    // AudioInputChunk
    /////////////////////////////////////////////////////////////////////////////////
    auto audinpchunk_t = py::class_<AudioInputChunk, audioinputchunk_ptr_t>(lev2_module, "AudioInputChunk")
        .def(py::init<size_t>(), py::arg("num_channels") = 1)
        .def_readwrite("num_frames", &AudioInputChunk::_num_frames)
        .def_readwrite("chunk_index", &AudioInputChunk::_chunk_index)
        .def_readwrite("timestamp", &AudioInputChunk::_timestamp)
        .def("setChannelData", [](audioinputchunk_ptr_t chunk, size_t channel, py::array_t<float> data) {
          auto buf = data.request();
          if (channel >= chunk->_channels.size()) {
            throw std::out_of_range("channel index out of range");
          }
          auto* ptr = static_cast<float*>(buf.ptr);
          size_t count = buf.size;
          chunk->_channels[channel].assign(ptr, ptr + count);
          chunk->_num_frames = count;
        }, py::arg("channel"), py::arg("data"));
    type_codec->registerStdCodec<audioinputchunk_ptr_t>(audinpchunk_t);
    /////////////////////////////////////////////////////////////////////////////////
    auto audinpsrc_t = py::class_<AudioInputChunkSource, audioinputchunk_source_ptr_t>(lev2_module, "AudioInputChunkSource"); //
    type_codec->registerStdCodec<audioinputchunk_source_ptr_t>(audinpsrc_t);
    /////////////////////////////////////////////////////////////////////////////////
    auto straudinpsrc_t = py::class_<StreamingAudioInputChunkSource, AudioInputChunkSource, audiostreaminginputchunk_source_ptr_t>(lev2_module, "StreamingAudioInputChunkSource")
        .def(py::init([]() {
          return std::make_shared<StreamingAudioInputChunkSource>();
        }))
        .def("pushChunk", [](audiostreaminginputchunk_source_ptr_t src, audioinputchunk_ptr_t chunk) {
          src->_inputqueue.push(chunk);
        }, py::arg("chunk"))
        .def("tryPushChunk", [](audiostreaminginputchunk_source_ptr_t src, audioinputchunk_ptr_t chunk) -> bool {
          return src->_inputqueue.try_push(chunk);
        }, py::arg("chunk"))
        .def_property_readonly("current_playback_timestamp", [](audiostreaminginputchunk_source_ptr_t src) -> double {
          return src->_current_playback_timestamp.load(std::memory_order_relaxed);
        })
        .def_property_readonly("ring_buffer_samples", [](audiostreaminginputchunk_source_ptr_t src) -> int {
          return src->_ring_buffer_samples.load(std::memory_order_relaxed);
        })
        .def_property_readonly("dsp_sample_rate", [](audiostreaminginputchunk_source_ptr_t src) -> float {
          return src->_dsp_sample_rate.load(std::memory_order_relaxed);
        })
        .def_property_readonly("actual_playback_timestamp", [](audiostreaminginputchunk_source_ptr_t src) -> double {
          // Estimate what's actually coming out of the speakers right now:
          // last-dequeued chunk timestamp minus ring buffer depth in seconds.
          double ts = src->_current_playback_timestamp.load(std::memory_order_relaxed);
          int buf_samples = src->_ring_buffer_samples.load(std::memory_order_relaxed);
          float sr = src->_dsp_sample_rate.load(std::memory_order_relaxed);
          if (sr <= 0.0f) sr = 48000.0f;
          return ts - double(buf_samples) / double(sr);
        });
    type_codec->registerStdCodec<audiostreaminginputchunk_source_ptr_t>(straudinpsrc_t);
    /////////////////////////////////////////////////////////////////////////////////
    // AudioFrameCapture
    /////////////////////////////////////////////////////////////////////////////////
    auto audioframecap_t = py::class_<AudioFrameCapture, audioframecapture_ptr_t>(lev2_module, "AudioFrameCapture")
        .def(py::init<>())
        .def_readonly("left", &AudioFrameCapture::_left)
        .def_readonly("right", &AudioFrameCapture::_right)
        .def_readonly("num_samples", &AudioFrameCapture::_num_samples)
        .def_readonly("sample_rate", &AudioFrameCapture::_sample_rate)
        .def_readonly("timestamp", &AudioFrameCapture::_timestamp);
    type_codec->registerStdCodec<audioframecapture_ptr_t>(audioframecap_t);
    /////////////////////////////////////////////////////////////////////////////////
    // StrAudioDevice
    /////////////////////////////////////////////////////////////////////////////////
    py::enum_<StrAudioDevice::Mode>(lev2_module, "StrAudioDeviceMode")
        .value("ASYNC_REALTIME", StrAudioDevice::Mode::ASYNC_REALTIME)
        .value("SYNC_NONREALTIME", StrAudioDevice::Mode::SYNC_NONREALTIME)
        .export_values();
       
    using strauddec_ptr_t = std::shared_ptr<StrAudioDevice>;
    auto straudiodev_t = py::class_<StrAudioDevice, AudioDevice, strauddec_ptr_t>(lev2_module, "StrAudioDevice")
        .def("advanceTime", &StrAudioDevice::advanceTime, py::arg("dt_seconds"))
        .def("extractSamples", &StrAudioDevice::extractSamples, py::arg("num_samples"))
        .def("availableSamples", &StrAudioDevice::availableSamples)
        .def("currentTime", &StrAudioDevice::currentTime)
        .def_readwrite("mode", &StrAudioDevice::_mode);
    type_codec->registerStdCodec<strauddec_ptr_t>(straudiodev_t);
    }
///////////////////////////////////////////////////////////////////////////////
} //namespace ork::lev2 {
