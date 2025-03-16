from flask import Flask, request, send_file, jsonify, render_template
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
import io
import math
import os
import re
import threading
import uuid
import time

app = Flask(__name__)
upload_temporary_folder = 'temp'
app.config['UPLOAD_FOLDER'] = upload_temporary_folder
processing_jobs = {}

def generateUniqueFilename(original_file_path):
    directory_name, original_file_name = os.path.split(original_file_path)
    base_file_name, file_extension = os.path.splitext(original_file_name)
    if not os.path.exists(original_file_path):
        return original_file_path
    filename_pattern = re.compile(rf"{re.escape(base_file_name)} \((\d+)\){re.escape(file_extension)}")
    maximum_number_found = 0
    for existing_file in os.listdir(directory_name):
        match_result = filename_pattern.match(existing_file)
        if match_result:
            extracted_number = int(match_result.group(1))
            maximum_number_found = max(maximum_number_found, extracted_number)
    new_file_name = f"{base_file_name} ({maximum_number_found + 1}){file_extension}"
    return os.path.join(directory_name, new_file_name)

def backgroundEncodeProcessing(job_identifier, file_content, original_filename):
    try:
        file_extension_bytes = os.path.splitext(original_filename)[1].encode('utf-8')
        file_extension_length_bytes = len(file_extension_bytes).to_bytes(1, 'little')
        file_content_size_bytes = len(file_content).to_bytes(4, 'little')
        combined_file_bytes = file_extension_length_bytes + file_extension_bytes + file_content_size_bytes + file_content
        total_data_length = len(combined_file_bytes)
        image_dimension = math.ceil(math.sqrt(total_data_length / 4))
        created_image = Image.new("RGBA", (image_dimension, image_dimension))
        pixel_access_object = created_image.load()
        total_rows = image_dimension
        current_byte_index = 0
        for row_index in range(image_dimension):
            if processing_jobs[job_identifier].get('cancelled'):
                processing_jobs[job_identifier]['status'] = 'cancelled'
                return
            for column_index in range(image_dimension):
                if current_byte_index + 4 <= len(combined_file_bytes):
                    red_channel_value = combined_file_bytes[current_byte_index]
                    green_channel_value = combined_file_bytes[current_byte_index + 1]
                    blue_channel_value = combined_file_bytes[current_byte_index + 2]
                    alpha_channel_value = combined_file_bytes[current_byte_index + 3]
                    current_byte_index += 4
                else:
                    red_channel_value = 0
                    green_channel_value = 0
                    blue_channel_value = 0
                    alpha_channel_value = 0
                pixel_access_object[column_index, row_index] = (red_channel_value, green_channel_value, blue_channel_value, alpha_channel_value)
            processing_jobs[job_identifier]['progress'] = int(((row_index + 1) / total_rows) * 100)
            time.sleep(0.01)
        image_output_stream = io.BytesIO()
        created_image.save(image_output_stream, 'PNG')
        image_output_stream.seek(0)
        result_file_bytes = image_output_stream.read()
        download_filename = generateUniqueFilename(f"encoded_{original_filename}.png")
        processing_jobs[job_identifier]['result'] = result_file_bytes
        processing_jobs[job_identifier]['download_filename'] = download_filename
        processing_jobs[job_identifier]['mimetype'] = 'image/png'
        processing_jobs[job_identifier]['status'] = 'complete'
    except Exception as error_during_processing:
        processing_jobs[job_identifier]['status'] = 'error'
        processing_jobs[job_identifier]['error'] = str(error_during_processing)

def backgroundDecodeProcessing(job_identifier, png_file_bytes, original_filename):
    try:
        loaded_image = Image.open(io.BytesIO(png_file_bytes))
        if loaded_image.mode != "RGBA":
            loaded_image = loaded_image.convert("RGBA")
        flattened_pixel_data_list = list(loaded_image.getdata())
        reconstructed_byte_array = bytearray()
        total_rows = loaded_image.height
        for row_index in range(loaded_image.height):
            if processing_jobs[job_identifier].get('cancelled'):
                processing_jobs[job_identifier]['status'] = 'cancelled'
                return
            start_index = row_index * loaded_image.width
            end_index = start_index + loaded_image.width
            for pixel_value in flattened_pixel_data_list[start_index:end_index]:
                reconstructed_byte_array.extend(pixel_value)
            processing_jobs[job_identifier]['progress'] = int(((row_index + 1) / total_rows) * 100)
            time.sleep(0.01)
        file_extension_length_extracted = reconstructed_byte_array[0]
        file_extension_extracted = bytes(reconstructed_byte_array[1:1 + file_extension_length_extracted]).decode('utf-8')
        file_content_size_extracted = int.from_bytes(bytes(reconstructed_byte_array[1 + file_extension_length_extracted:1 + file_extension_length_extracted + 4]), 'little')
        extracted_file_content_bytes = bytes(reconstructed_byte_array[1 + file_extension_length_extracted + 4:1 + file_extension_length_extracted + 4 + file_content_size_extracted])
        download_filename = generateUniqueFilename(f"decoded_file{file_extension_extracted}")
        processing_jobs[job_identifier]['result'] = extracted_file_content_bytes
        processing_jobs[job_identifier]['download_filename'] = download_filename
        processing_jobs[job_identifier]['mimetype'] = 'application/octet-stream'
        processing_jobs[job_identifier]['status'] = 'complete'
    except Exception as error_during_processing:
        processing_jobs[job_identifier]['status'] = 'error'
        processing_jobs[job_identifier]['error'] = str(error_during_processing)

@app.route('/encode', methods=['POST'])
def startEncodeProcessing():
    try:
        selected_upload_file = request.files['file']
        file_content = selected_upload_file.read()
        job_identifier = str(uuid.uuid4())
        processing_jobs[job_identifier] = {'progress': 0, 'status': 'processing', 'cancelled': False}
        thread_for_processing = threading.Thread(target=backgroundEncodeProcessing, args=(job_identifier, file_content, selected_upload_file.filename))
        thread_for_processing.start()
        return jsonify(job_id=job_identifier)
    except Exception as error_during_upload:
        return jsonify(error=str(error_during_upload)), 500

@app.route('/decode', methods=['POST'])
def startDecodeProcessing():
    try:
        selected_png_file = request.files['file']
        png_file_bytes = selected_png_file.read()
        job_identifier = str(uuid.uuid4())
        processing_jobs[job_identifier] = {'progress': 0, 'status': 'processing', 'cancelled': False}
        thread_for_processing = threading.Thread(target=backgroundDecodeProcessing, args=(job_identifier, png_file_bytes, selected_png_file.filename))
        thread_for_processing.start()
        return jsonify(job_id=job_identifier)
    except Exception as error_during_upload:
        return jsonify(error=str(error_during_upload)), 500

@app.route('/cancel/<job_identifier>', methods=['POST'])
def cancelJob(job_identifier):
    job_status = processing_jobs.get(job_identifier)
    if not job_status:
        return jsonify(error='Job not found'), 404
    processing_jobs[job_identifier]['cancelled'] = True
    return jsonify(status='cancelled')

@app.route('/status/<job_identifier>')
def getProcessingStatus(job_identifier):
    job_status = processing_jobs.get(job_identifier)
    if not job_status:
        return jsonify(error='Job not found'), 404
    return jsonify(progress=job_status.get('progress', 0), status=job_status.get('status', 'processing'), error=job_status.get('error', ''))

@app.route('/download/<job_identifier>')
def downloadProcessedFile(job_identifier):
    job_status = processing_jobs.get(job_identifier)
    if not job_status or job_status.get('status') != 'complete':
        return jsonify(error='Processing not complete'), 400
    result_file_bytes = job_status['result']
    download_filename = job_status['download_filename']
    mimetype_value = job_status['mimetype']
    return send_file(io.BytesIO(result_file_bytes), mimetype=mimetype_value, as_attachment=True, download_name=download_filename)

@app.route('/')
def renderIndexPage():
    return render_template('index.html')

if __name__ == '__main__':
    os.makedirs(upload_temporary_folder, exist_ok=True)
    app.run(debug=True)
