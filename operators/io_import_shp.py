# -*- coding:utf-8 -*-
import os, sys, time
import bpy
from bpy.props import StringProperty, BoolProperty, EnumProperty, IntProperty
from bpy.types import Operator
import bmesh
import math
from mathutils import Vector

import logging
log = logging.getLogger(__name__)

from ..core.lib.shapefile import Reader as shpReader

from ..geoscene import GeoScene, georefManagerLayout
from ..prefs import PredefCRS
from ..core import BBOX
from ..core.proj import Reproj
from ..core.utils import perf_clock

from .utils import adjust3Dview, getBBOX, DropToGround

PKG, SUBPKG = __package__.split('.', maxsplit=1)

featureType={
0:'Null',
1:'Point',
3:'PolyLine',
5:'Polygon',
8:'MultiPoint',
11:'PointZ',
13:'PolyLineZ',
15:'PolygonZ',
18:'MultiPointZ',
21:'PointM',
23:'PolyLineM',
25:'PolygonM',
28:'MultiPointM',
31:'MultiPatch'
}


"""
dbf fields type:
	C is ASCII characters
	N is a double precision integer limited to around 18 characters in length
	D is for dates in the YYYYMMDD format, with no spaces or hyphens between the sections
	F is for floating point numbers with the same length limits as N
	L is for logical data which is stored in the shapefile's attribute table as a short integer as a 1 (true) or a 0 (false).
	The values it can receive are 1, 0, y, n, Y, N, T, F or the python builtins True and False
"""


class IMPORTGIS_OT_shapefile_file_dialog(Operator):
	"""Select shp file, loads the fields and start importgis.shapefile_props_dialog operator"""

	bl_idname = "importgis.shapefile_file_dialog"
	bl_description = 'Import ESRI shapefile (.shp)'
	bl_label = "Import SHP"
	bl_options = {'INTERNAL'}

	# Import dialog properties
	filepath: StringProperty(
		name="File Path",
		description="Filepath used for importing the file",
		maxlen=1024,
		subtype='FILE_PATH' )

	filename_ext = ".shp"

	filter_glob: StringProperty(
			default = "*.shp",
			options = {'HIDDEN'} )

	def invoke(self, context, event):
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}

	def draw(self, context):
		layout = self.layout
		layout.label(text="Options will be available")
		layout.label(text="after selecting a file")

	def execute(self, context):
		if os.path.exists(self.filepath):
			bpy.ops.importgis.shapefile_props_dialog('INVOKE_DEFAULT', filepath=self.filepath)
		else:
			self.report({'ERROR'}, "Invalid filepath")
		return{'FINISHED'}



class IMPORTGIS_OT_shapefile_props_dialog(Operator):
	"""Shapefile importer properties dialog"""

	bl_idname = "importgis.shapefile_props_dialog"
	bl_description = 'Import ESRI shapefile (.shp)'
	bl_label = "Import SHP"
	bl_options = {"INTERNAL"}

	filepath: StringProperty()

	#special function to auto redraw an operator popup called through invoke_props_dialog
	def check(self, context):
		return True

	def listFields(self, context):
		fieldsItems = []
		try:
			shp = shpReader(self.filepath)
		except Exception as e:
			log.warning("Unable to read shapefile fields", exc_info=True)
			return fieldsItems
		fields = [field for field in shp.fields if field[0] != 'DeletionFlag'] #ignore default DeletionFlag field
		for i, field in enumerate(fields):
			#put each item in a tuple (key, label, tooltip)
			fieldsItems.append( (field[0], field[0], '') )
		return fieldsItems

	# Shapefile CRS definition
	def listPredefCRS(self, context):
		return PredefCRS.getEnumItems()

	def listObjects(self, context):
		objs = []
		for index, object in enumerate(bpy.context.scene.objects):
			if object.type == 'MESH':
				#put each object in a tuple (key, label, tooltip) and add this to the objects list
				objs.append((object.name, object.name, "Object named " + object.name))
		return objs

	reprojection: BoolProperty(
			name="Specifiy shapefile CRS",
			description="Specifiy shapefile CRS if it's different from scene CRS",
			default=False )

	shpCRS: EnumProperty(
		name = "Shapefile CRS",
		description = "Choose a Coordinate Reference System",
		items = listPredefCRS)

	# Elevation source
	vertsElevSource: EnumProperty(
			name="Elevation source",
			description="Select the source of vertices z value",
			items=[
			('NONE', 'None', "Flat geometry"),
			('GEOM', 'Geometry', "Use z value from shape geometry if exists"),
			('FIELD', 'Field', "Extract z elevation value from an attribute field"),
			('OBJ', 'Object', "Get z elevation value from an existing ground mesh")
			],
			default='GEOM')

	# Elevation object
	objElevLst: EnumProperty(
		name="Elev. object",
		description="Choose the mesh from which extract z elevation",
		items=listObjects )

	# Elevation field
	'''
	useFieldElev: BoolProperty(
			name="Elevation from field",
			description="Extract z elevation value from an attribute field",
			default=False )
	'''
	fieldElevName: EnumProperty(
		name = "Elev. field",
		description = "Choose field",
		items = listFields )

	#Extrusion field
	useFieldExtrude: BoolProperty(
			name="Extrusion from field",
			description="Extract z extrusion value from an attribute field",
			default=False )

	fieldExtrudeName: EnumProperty(
		name = "Field",
		description = "Choose field",
		items = listFields )

	#Extrusion axis
	extrusionAxis: EnumProperty(
			name="Extrude along",
			description="Select extrusion axis",
			items=[ ('Z', 'z axis', "Extrude along Z axis"),
			('NORMAL', 'Normal', "Extrude along normal")] )

	#Create separate objects
	separateObjects: BoolProperty(
			name="Separate objects",
			description="Warning : can be very slow with lot of features",
			default=False )

	#Name objects from field
	useFieldName: BoolProperty(
			name="Object name from field",
			description="Extract name for created objects from an attribute field",
			default=False )
	fieldObjName: EnumProperty(
		name = "Field",
		description = "Choose field",
		items = listFields )


	def draw(self, context):
		#Function used by blender to draw the panel.
		scn = context.scene
		layout = self.layout

		#
		layout.prop(self, 'vertsElevSource')
		#
		#layout.prop(self, 'useFieldElev')
		if self.vertsElevSource == 'FIELD':
			layout.prop(self, 'fieldElevName')
		elif self.vertsElevSource == 'OBJ':
			layout.prop(self, 'objElevLst')
		#
		layout.prop(self, 'useFieldExtrude')
		if self.useFieldExtrude:
			layout.prop(self, 'fieldExtrudeName')
			layout.prop(self, 'extrusionAxis')
		#
		layout.prop(self, 'separateObjects')
		if self.separateObjects:
			layout.prop(self, 'useFieldName')
		else:
			self.useFieldName = False
		if self.separateObjects and self.useFieldName:
			layout.prop(self, 'fieldObjName')
		#
		geoscn = GeoScene()
		#geoscnPrefs = context.preferences.addons['geoscene'].preferences
		if geoscn.isPartiallyGeoref:
			layout.prop(self, 'reprojection')
			if self.reprojection:
				self.shpCRSInputLayout(context)
			#
			georefManagerLayout(self, context)
		else:
			self.shpCRSInputLayout(context)


	def shpCRSInputLayout(self, context):
		layout = self.layout
		row = layout.row(align=True)
		#row.prop(self, "shpCRS", text='CRS')
		split = row.split(factor=0.35, align=True)
		split.label(text='CRS:')
		split.prop(self, "shpCRS", text='')
		row.operator("bgis.add_predef_crs", text='', icon='ADD')


	def invoke(self, context, event):
		return context.window_manager.invoke_props_dialog(self)

	def execute(self, context):

		#elevField = self.fieldElevName if self.useFieldElev else ""
		elevField = self.fieldElevName if self.vertsElevSource == 'FIELD' else ""
		extrudField = self.fieldExtrudeName if self.useFieldExtrude else ""
		nameField = self.fieldObjName if self.useFieldName else ""
		if self.vertsElevSource == 'OBJ':
			if not self.objElevLst:
				self.report({'ERROR'}, "No elevation object")
				return {'CANCELLED'}
			else:
				objElevName = self.objElevLst
		else:
			objElevName = '' #will not be used

		geoscn = GeoScene()
		if geoscn.isBroken:
			self.report({'ERROR'}, "Scene georef is broken, please fix it beforehand")
			return {'CANCELLED'}

		if geoscn.isGeoref:
			if self.reprojection:
				shpCRS = self.shpCRS
			else:
				shpCRS = geoscn.crs
		else:
			shpCRS = self.shpCRS

		try:
			bpy.ops.importgis.shapefile('INVOKE_DEFAULT', filepath=self.filepath, shpCRS=shpCRS, elevSource=self.vertsElevSource,
				fieldElevName=elevField, objElevName=objElevName, fieldExtrudeName=extrudField, fieldObjName=nameField,
				extrusionAxis=self.extrusionAxis, separateObjects=self.separateObjects)
		except Exception as e:
			log.error('Shapefile import fails', exc_info=True)
			self.report({'ERROR'}, 'Shapefile import fails, check logs.')
			return {'CANCELLED'}

		return{'FINISHED'}


class IMPORTGIS_OT_shapefile(Operator):
	"""Import from ESRI shapefile file format (.shp)"""

	bl_idname = "importgis.shapefile" # important since its how bpy.ops.import.shapefile is constructed (allows calling operator from python console or another script)
	#bl_idname rules: must contain one '.' (dot) charactere, no capital letters, no reserved words (like 'import')
	bl_description = 'Import ESRI shapefile (.shp)'
	bl_label = "Import SHP"
	bl_options = {"UNDO"}

	filepath: StringProperty()

	shpCRS: StringProperty(name = "Shapefile CRS", description = "Coordinate Reference System")

	elevSource: StringProperty(name = "Elevation source", description = "Elevation source", default='GEOM') # [NONE, GEOM, OBJ, FIELD]
	objElevName: StringProperty(name = "Elevation object name", description = "")

	fieldElevName: StringProperty(name = "Elevation field", description = "Field name")
	fieldExtrudeName: StringProperty(name = "Extrusion field", description = "Field name")
	fieldObjName: StringProperty(name = "Objects names field", description = "Field name")

	#Extrusion axis
	extrusionAxis: EnumProperty(
			name="Extrude along",
			description="Select extrusion axis",
			items=[ ('Z', 'z axis', "Extrude along Z axis"),
			('NORMAL', 'Normal', "Extrude along normal")]
			)
	#Create separate objects
	separateObjects: BoolProperty(
			name="Separate objects",
			description="Import to separate objects instead one large object",
			default=False
			)

	@classmethod
	def poll(cls, context):
		return context.mode == 'OBJECT'

	def _setup_import(self, context):
		"""Initialize shapefile import state. Returns True on success, False on failure."""
		prefs = bpy.context.preferences.addons[PKG].preferences
		self._prefs = prefs

		#Path
		self._shpName = os.path.basename(self.filepath)[:-4]

		#Get shp reader
		log.info("Read shapefile...")
		try:
			self._shp = shpReader(self.filepath)
		except Exception as e:
			log.error("Unable to read shapefile", exc_info=True)
			self.report({'ERROR'}, "Unable to read shapefile, check logs")
			return False

		#Check shape type
		self._shpType = featureType[self._shp.shapeType]
		log.info('Feature type : ' + self._shpType)
		if self._shpType not in ['Point','PolyLine','Polygon','PointZ','PolyLineZ','PolygonZ']:
			self.report({'ERROR'}, "Cannot process multipoint, multipointZ, pointM, polylineM, polygonM and multipatch feature type")
			self._cleanup_setup()
			return False

		if self.elevSource != 'FIELD':
			self.fieldElevName = ''

		if self.elevSource == 'OBJ':
			scn = bpy.context.scene
			elevObj = scn.objects[self.objElevName]
			self._rayCaster = DropToGround(scn, elevObj)
		else:
			self._rayCaster = None

		#Get fields
		self._fields = [field for field in self._shp.fields if field[0] != 'DeletionFlag'] #ignore default DeletionFlag field
		self._fieldsNames = [field[0] for field in self._fields]
		log.debug("DBF fields : "+str(self._fieldsNames))

		if self.separateObjects or self.fieldElevName or self.fieldObjName or self.fieldExtrudeName:
			self.useDbf = True
		else:
			self.useDbf = False

		if self.fieldObjName and self.separateObjects:
			try:
				self._nameFieldIdx = self._fieldsNames.index(self.fieldObjName)
			except Exception as e:
				log.error('Unable to find name field', exc_info=True)
				self.report({'ERROR'}, "Unable to find name field")
				self._cleanup_setup()
				return False
		else:
			self._nameFieldIdx = None

		if self.fieldElevName:
			try:
				self._zFieldIdx = self._fieldsNames.index(self.fieldElevName)
			except Exception as e:
				log.error('Unable to find elevation field', exc_info=True)
				self.report({'ERROR'}, "Unable to find elevation field")
				self._cleanup_setup()
				return False

			if self._fields[self._zFieldIdx][1] not in ['N', 'F', 'L'] :
				self.report({'ERROR'}, "Elevation field do not contains numeric values")
				self._cleanup_setup()
				return False
		else:
			self._zFieldIdx = None

		if self.fieldExtrudeName:
			try:
				self._extrudeFieldIdx = self._fieldsNames.index(self.fieldExtrudeName)
			except ValueError:
				log.error('Unable to find extrusion field', exc_info=True)
				self.report({'ERROR'}, "Unable to find extrusion field")
				self._cleanup_setup()
				return False

			if self._fields[self._extrudeFieldIdx][1] not in ['N', 'F', 'L'] :
				self.report({'ERROR'}, "Extrusion field do not contains numeric values")
				self._cleanup_setup()
				return False
		else:
			self._extrudeFieldIdx = None

		#Get shp and scene georef infos
		shpCRS = self.shpCRS
		self._geoscn = GeoScene()
		if self._geoscn.isBroken:
			self.report({'ERROR'}, "Scene georef is broken, please fix it beforehand")
			self._cleanup_setup()
			return False

		self._scale = self._geoscn.scale #TODO

		if not self._geoscn.hasCRS: #if not self._geoscn.isGeoref:
			try:
				self._geoscn.crs = shpCRS
			except Exception as e:
				log.error("Cannot set scene crs", exc_info=True)
				self.report({'ERROR'}, "Cannot set scene crs, check logs for more infos")
				self._cleanup_setup()
				return False

		#Init reprojector class
		self._rprj = None
		if self._geoscn.crs != shpCRS:
			log.info("Data will be reprojected from {} to {}".format(shpCRS, self._geoscn.crs))
			try:
				self._rprj = Reproj(shpCRS, self._geoscn.crs)
			except Exception as e:
				log.error('Reprojection fails', exc_info=True)
				self.report({'ERROR'}, "Unable to reproject data, check logs for more infos.")
				self._cleanup_setup()
				return False
			if self._rprj.iproj == 'EPSGIO':
				if self._shp.numRecords > 100:
					self.report({'ERROR'}, "Reprojection through online epsg.io engine is limited to 100 features. \nPlease install GDAL or pyproj module.")
					self._cleanup_setup()
					return False

		#Get bbox
		self._bbox = BBOX(self._shp.bbox)
		if self._geoscn.crs != shpCRS:
			self._bbox = self._rprj.bbox(self._bbox)

		#Get or set georef dx, dy
		if not self._geoscn.isGeoref:
			dx, dy = self._bbox.center
			self._geoscn.setOriginPrj(dx, dy)
		else:
			dx, dy = self._geoscn.getOriginPrj()
		self._dx, self._dy = dx, dy

		#Get reader iterator (using iterator avoids loading all data in memory)
		#warn, shp with zero field will return an empty shapeRecords() iterator
		#to prevent this issue, iter only on shapes if there is no field required
		if self.useDbf:
			#Note: using shapeRecord solve the issue where number of shapes does not match number of table records
			#because it iter only on features with geom and record
			self._shpIter = self._shp.iterShapeRecords()
		else:
			self._shpIter = self._shp.iterShapes()
		self._nbFeats = self._shp.numRecords

		#Create an empty BMesh
		self._bm = bmesh.new()
		#Extrusion is exponentially slow with large bmesh
		#it's fastest to extrude a small bmesh and then join it to a final large bmesh
		if not self.separateObjects and self.fieldExtrudeName:
			self._finalBm = bmesh.new()
		else:
			self._finalBm = None

		if self.separateObjects:
			self._layer = bpy.data.collections.new(self._shpName)
			context.scene.collection.children.link(self._layer)
		else:
			self._layer = None

		self._currentIdx = 0
		return True

	def _cleanup_setup(self):
		"""Close shapefile reader if it was opened during a failed setup."""
		if hasattr(self, '_shp') and self._shp:
			try:
				self._shp.close()
			except Exception:
				pass
			self._shp = None

	def _process_feature(self, context, i):
		"""Process a single shapefile feature by index."""
		shp = self._shp
		shpType = self._shpType
		geoscn = self._geoscn
		dx, dy = self._dx, self._dy
		rprj = self._rprj
		bm = self._bm
		finalBm = self._finalBm
		layer = self._layer
		shpCRS = self.shpCRS
		prefs = self._prefs

		if self.useDbf:
			feat = shp.shapeRecord(i)
			shape = feat.shape
			record = feat.record
		else:
			shape = shp.shape(i)
			record = None

		#Deal with multipart features
		#If the shape record has multiple parts, the 'parts' attribute will contains the index of
		#the first point of each part. If there is only one part then a list containing 0 is returned
		if (shpType == 'PointZ' or shpType == 'Point'): #point layer has no attribute 'parts'
			partsIdx = [0]
		else:
			try: #prevent "_shape object has no attribute parts" error
				partsIdx = shape.parts
			except Exception as e:
				log.warning('Cannot access "parts" attribute for feature {} : {}'.format(i, e))
				partsIdx = [0]
		nbParts = len(partsIdx)

		#Get list of shape's points
		pts = shape.points
		nbPts = len(pts)

		#Skip null geom
		if nbPts == 0:
			return

		#Reproj geom
		if geoscn.crs != shpCRS:
			pts = rprj.pts(pts)

		#Get extrusion offset
		offset = 0
		if self.fieldExtrudeName:
			try:
				offset = float(record[self._extrudeFieldIdx])
			except Exception as e:
				log.warning('Cannot extract extrusion value for feature {} : {}'.format(i, e))
				offset = 0 #null values will be set to zero

		#Iter over parts
		for j in range(nbParts):

			# EXTRACT 3D GEOM

			geom = [] #will contains a list of 3d points

			#Find first and last part index
			idx1 = partsIdx[j]
			if j+1 == nbParts:
				idx2 = nbPts
			else:
				idx2 = partsIdx[j+1]

			#Build 3d geom
			for k, pt in enumerate(pts[idx1:idx2]):

				if self.elevSource == 'OBJ':
					rcHit = self._rayCaster.rayCast(x=pt[0]-dx, y=pt[1]-dy)
					z = rcHit.loc.z #will be automatically set to zero if not rcHit.hit

				elif self.elevSource == 'FIELD':
					try:
						z = float(record[self._zFieldIdx])
					except Exception as e:
						log.warning('Cannot extract elevation value for feature {} : {}'.format(i, e))
						z = 0 #null values will be set to zero

				elif shpType[-1] == 'Z' and self.elevSource == 'GEOM':
					z = shape.z[idx1:idx2][k]

				else:
					z = 0

				geom.append((pt[0], pt[1], z))

			#Shift coords
			geom = [(pt[0]-dx, pt[1]-dy, pt[2]) for pt in geom]


			# BUILD BMESH

			# POINTS
			if (shpType == 'PointZ' or shpType == 'Point'):
				vert = [bm.verts.new(pt) for pt in geom]
				#Extrusion
				if self.fieldExtrudeName and offset > 0:
					vect = (0, 0, offset) #along Z
					result = bmesh.ops.extrude_vert_indiv(bm, verts=vert)
					verts = result['verts']
					bmesh.ops.translate(bm, verts=verts, vec=vect)

			# LINES
			if (shpType == 'PolyLine' or shpType == 'PolyLineZ'):
				verts = [bm.verts.new(pt) for pt in geom]
				edges = []
				for i2 in range(len(geom)-1):
					edge = bm.edges.new( [verts[i2], verts[i2+1] ])
					edges.append(edge)
				#Extrusion
				if self.fieldExtrudeName and offset > 0:
					vect = (0, 0, offset) # along Z
					result = bmesh.ops.extrude_edge_only(bm, edges=edges)
					verts = [elem for elem in result['geom'] if isinstance(elem, bmesh.types.BMVert)]
					bmesh.ops.translate(bm, verts=verts, vec=vect)

			# NGONS
			if (shpType == 'Polygon' or shpType == 'PolygonZ'):
				#According to the shapefile spec, polygons points are clockwise and polygon holes are counterclockwise
				#in Blender face is up if points are in anticlockwise order
				geom.reverse() #face up
				geom.pop() #exlude last point because it's the same as first pt
				if len(geom) >= 3: #needs 3 points to get a valid face
					verts = [bm.verts.new(pt) for pt in geom]
					face = bm.faces.new(verts)
					#update normal to avoid null vector
					face.normal_update()
					if face.normal.z < 0: #this is a polygon hole, bmesh cannot handle polygon hole
						pass #TODO
					#Extrusion
					if self.fieldExtrudeName and offset > 0:
						#build translate vector
						if self.extrusionAxis == 'NORMAL':
							normal = face.normal
							vect = normal * offset
						elif self.extrusionAxis == 'Z':
							vect = (0, 0, offset)
						faces = bmesh.ops.extrude_discrete_faces(bm, faces=[face]) #return {'faces': [BMFace]}
						verts = faces['faces'][0].verts
						if self.elevSource == 'OBJ':
							# Making flat roof (TODO add an user input parameter to setup this behaviour)
							z = max([v.co.z for v in verts]) + offset #get max z coord
							for v in verts:
								v.co.z = z
						else:
							##result = bmesh.ops.extrude_face_region(bm, geom=[face]) #return dict {"geom":[BMVert, BMEdge, BMFace]}
							##verts = [elem for elem in result['geom'] if isinstance(elem, bmesh.types.BMVert)] #geom type filter
							bmesh.ops.translate(bm, verts=verts, vec=vect)


		if self.separateObjects:

			if self.fieldObjName:
				try:
					name = record[self._nameFieldIdx]
				except Exception as e:
					log.warning('Cannot extract name value for feature {} : {}'.format(i, e))
					name = ''
				# null values will return a bytes object containing a blank string of length equal to fields length definition
				if isinstance(name, bytes):
					name = ''
				else:
					name = str(name)
			else:
				name = self._shpName

			#Calc bmesh bbox
			_bbox = getBBOX.fromBmesh(bm)

			#Calc bmesh geometry origin and translate coords according to it
			#then object location will be set to initial bmesh origin
			#its a work around to bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')
			ox, oy, oz = _bbox.center
			oz = _bbox.zmin
			bmesh.ops.translate(bm, verts=bm.verts, vec=(-ox, -oy, -oz))

			#Create new mesh from bmesh
			mesh = bpy.data.meshes.new(name)
			bm.to_mesh(mesh)
			bm.clear()

			#Validate new mesh
			mesh.validate(verbose=False)

			#Place obj
			obj = bpy.data.objects.new(name, mesh)
			layer.objects.link(obj)
			context.view_layer.objects.active = obj
			obj.select_set(True)
			obj.location = (ox, oy, oz)

			# bpy operators can be very cumbersome when scene contains lot of objects
			# because it cause implicit scene updates calls
			# so we must avoid using operators when created many objects with the 'separate objects' option)
			##bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')

			#write attributes data
			for fi, field in enumerate(shp.fields):
				fieldName, fieldType, fieldLength, fieldDecLength = field
				if fieldName != 'DeletionFlag':
					if fieldType in ('N', 'F'):
						v = record[fi-1]
						if v is not None:
							#cast to float to avoid overflow error when affecting custom property
							obj[fieldName] = float(record[fi-1])
					else:
						obj[fieldName] = record[fi-1]

		elif self.fieldExtrudeName:
			#Join to final bmesh (use from_mesh method hack)
			buff = bpy.data.meshes.new(".temp")
			bm.to_mesh(buff)
			finalBm.from_mesh(buff)
			bpy.data.meshes.remove(buff)
			bm.clear()

	def _finish_import(self, context):
		"""Finalize import after all features processed."""
		prefs = self._prefs
		shpName = self._shpName
		geoscn = self._geoscn
		bm = self._bm
		finalBm = self._finalBm
		dx, dy = self._dx, self._dy
		bbox = self._bbox

		#Write back the whole mesh
		if not self.separateObjects:

			mesh = bpy.data.meshes.new(shpName)

			if self.fieldExtrudeName:
				bm.free()
				bm = finalBm

			if prefs.mergeDoubles:
				bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
			bm.to_mesh(mesh)

			#Finish
			#mesh.update(calc_edges=True)
			mesh.validate(verbose=False) #return true if the mesh has been corrected
			obj = bpy.data.objects.new(shpName, mesh)
			context.scene.collection.objects.link(obj)
			context.view_layer.objects.active = obj
			obj.select_set(True)
			bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY')

		#free the bmesh
		bm.free()
		if finalBm:
			finalBm.free()

		#Adjust grid size
		if prefs.adjust3Dview:
			bbox.shift(-dx, -dy) #convert shapefile bbox in 3d view space
			adjust3Dview(context, bbox)

		# Close shapefile reader to release file handles
		try:
			self._shp.close()
		except Exception:
			pass

		log.info('Build finished')

	def execute(self, context):
		"""Synchronous fallback path (e.g. when called from scripts)."""
		prefs = bpy.context.preferences.addons[PKG].preferences
		w = context.window
		w.cursor_set('WAIT')
		t0 = perf_clock()

		bpy.ops.object.select_all(action='DESELECT')

		if not self._setup_import(context):
			w.cursor_set('DEFAULT')
			return {'CANCELLED'}

		wm = context.window_manager
		wm.progress_begin(0, self._nbFeats)

		for i in range(self._nbFeats):
			self._process_feature(context, i)
			wm.progress_update(i + 1)

		self._finish_import(context)
		wm.progress_end()

		t = perf_clock() - t0
		log.info('Build in %f seconds' % t)
		w.cursor_set('DEFAULT')
		return {'FINISHED'}

	def invoke(self, context, event):
		"""Start modal import to keep Blender responsive."""
		bpy.ops.object.select_all(action='DESELECT')

		if not self._setup_import(context):
			return {'CANCELLED'}

		self._t0 = perf_clock()
		self._batch_size = 20  # features per modal tick
		self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
		context.window_manager.modal_handler_add(self)
		context.window_manager.progress_begin(0, self._nbFeats)
		context.window.cursor_set('WAIT')
		return {'RUNNING_MODAL'}

	def modal(self, context, event):
		if event.type in {'RIGHTMOUSE', 'ESC'}:
			self.cancel(context)
			return {'CANCELLED'}

		wm = context.window_manager
		endIdx = min(self._currentIdx + self._batch_size, self._nbFeats)

		for i in range(self._currentIdx, endIdx):
			self._process_feature(context, i)

		self._currentIdx = endIdx
		wm.progress_update(self._currentIdx)

		if self._currentIdx >= self._nbFeats:
			self._finish_import(context)
			wm.progress_end()
			wm.event_timer_remove(self._timer)
			context.window.cursor_set('DEFAULT')
			t = perf_clock() - self._t0
			log.info('Build in %f seconds' % t)
			return {'FINISHED'}

		return {'PASS_THROUGH'}

	def cancel(self, context):
		wm = context.window_manager
		wm.progress_end()
		if hasattr(self, '_timer'):
			wm.event_timer_remove(self._timer)
		context.window.cursor_set('DEFAULT')
		# Clean up bmeshes
		if hasattr(self, '_bm') and self._bm:
			self._bm.free()
		if hasattr(self, '_finalBm') and self._finalBm:
			self._finalBm.free()
		# Close shapefile reader
		if hasattr(self, '_shp') and self._shp:
			try:
				self._shp.close()
			except Exception:
				pass
		log.info('Import cancelled by user')

classes = [
	IMPORTGIS_OT_shapefile_file_dialog,
	IMPORTGIS_OT_shapefile_props_dialog,
	IMPORTGIS_OT_shapefile
]

def register():
	for cls in classes:
		try:
			bpy.utils.register_class(cls)
		except ValueError as e:
			log.warning('{} is already registered, now unregister and retry... '.format(cls))
			bpy.utils.unregister_class(cls)
			bpy.utils.register_class(cls)

def unregister():
	for cls in classes:
		bpy.utils.unregister_class(cls)
