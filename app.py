import numpy as np
from apikey.client import Client
from copy import copy
from robot import Robot
import os

# You should fil creds.json with API key for your acount obtained from
# https://dev-portal.onshape.com/keys

# The first part in assembly should be the trunk
# The sub-parts should revolute around Z-axis
# DOFs should be named "dof..."
# Assembly should be named "robot"

# XXX: TODO:
# - Tester sur un robot plus complexe (Metabot?)
# - Les masses quasi nulles dans les link master c'est pas terrible, peut-on faire mieux?

client = Client(logging=False)

# Document that should be parsed
# XXX: Argv-ize
documentId = '483c803918afc4d52e2647f0'

print('* Retrieving workspace ID ...')
document = client.get_document(documentId).json()
workspaceId = document['defaultWorkspace']['id']
print('- Workspace id: '+workspaceId)

print('* Retrieving elements in the document, searching for the assembly...')
elements = client.list_elements(documentId).json()
assemblyId = None
for element in elements:
    if element['type'] == 'Assembly' and element['name'].lower() == 'robot':
        print("- Found assembly, id: "+element['id']+', name: "'+element['name']+'"')
        assemblyId = element['id']

if assemblyId == None:
    print("! Unable to find assembly in this document")
    exit(1)

print('* Retrieving assembly')
assembly = client.get_assembly(documentId, workspaceId, assemblyId).json()

# Collecting parts instance from assembly and subassemblies
instances = {}
def collectParts(instancesToWalk):
    for instance in instancesToWalk:
        instances[instance['id']] = instance

root = assembly['rootAssembly']
collectParts(root['instances'])
for asm in assembly['subAssemblies']:
    collectParts(asm['instances'])

# Collecting occurences
occurrences = {}
for occurrence in root['occurrences']:
    occurrence['parent'] = None
    occurrence['instance'] = instances[occurrence['path'][-1]]
    occurrence['transform'] = np.matrix(np.reshape(occurrence['transform'], (4, 4)))
    occurrences[tuple(occurrence['path'])] = occurrence

# Gets an occurrence given its path
def getOccurrence(path):
    return occurrences[tuple(path)]

def assignParts(root, parent):
    for occurrence in occurrences.values():
        if occurrence['path'][0] == root:
            occurrence['parent'] = parent

print('* Getting assembly features, scanning for DOFs...')
relations = []
features = root['features']
for feature in features:
    data = feature['featureData']
    if data['name'][0:3] == 'dof':
        a = data['matedEntities'][0]['matedOccurrence'][0]
        b = data['matedEntities'][1]['matedOccurrence'][0]
        relations.append([a, b, data])
        assignParts(a, a)
        assignParts(b, b)
print('- Found '+str(len(relations))+' DOFs')

for feature in features:
    data = feature['featureData']
    a = data['matedEntities'][0]['matedOccurrence'][0]
    b = data['matedEntities'][1]['matedOccurrence'][0]
    
    
print('* Building robot tree')
def collect(id, parent = None):
    part = {}
    part['id'] = id
    part['children'] = []
    for entry in relations:
        if entry[0] == id and entry[1] != parent:
            child = collect(entry[1], id)
            child['mate'] = entry[2]
            part['children'].append(child)
        if entry[1] == id and entry[0] != parent:
            child = collect(entry[0], id)
            child['mate'] = entry[2]
            part['children'].append(child)
    return part

trunk = root['instances'][0]['id']
tree = collect(trunk)

robot = Robot()

def addPart(link, occurrence, matrix, name):
    part = occurrence['instance']
    # Importing STL file for this part
    stlFile = '%s_%s_%s_%s.stl' % (part['documentId'], part['documentMicroversion'], part['elementId'], part['partId'])
    if not os.path.exists('urdf/'+stlFile):
        stl = client.part_studio_stl_m(part['documentId'], part['documentMicroversion'], part['elementId'], part['partId'])
        f = open('urdf/'+stlFile, 'wb')
        f.write(stl.content)
        f.close()
        
    massProperties = client.part_mas_properties(part['documentId'], part['documentMicroversion'], part['elementId'], part['partId']).json()
    massProperties = massProperties['bodies'][part['partId']]
    mass = massProperties['mass'][0]
    com = massProperties['centroid']
    inertia = massProperties['inertia']
    robot.addLink(link, name, np.linalg.inv(matrix)*occurrence['transform'], stlFile, mass, com, inertia)

def buildRobot(tree, matrix, linkPart=None):
    print('~~~ Adding instance')
    occurrence = getOccurrence([tree['id']])
    instance = occurrence['instance']
    print('> '+instance['name'])
    link = instance['name']
    link = link.split(' ')[0].lower()
    
    print('DoubleArm, link: '+str(linkPart))

    if instance['type'] == 'part':
        # XXX: Redundant code
        name = '_'.join(occurrence['path'])
        if linkPart == None:
            linkPart = name
        if name == linkPart:
            name = link
        addPart(link, occurrence, matrix, name)
    else:
        # The instance is probably an assembly, gathering everything that
        # begins with the same path
        for occurrence in occurrences.values():
            if occurrence['path'][0] == tree['id'] and occurrence['instance']['type'] == 'Part':
                name = '_'.join(occurrence['path'])
                print('Some part, name: '+name)
                if linkPart == None:
                    linkPart = name
                if name == linkPart:
                    name = link
                addPart(link, occurrence, matrix, name)

    for child in tree['children']:
        mate = child['mate']
        if mate['matedEntities'][0]['matedOccurrence'][0] == tree['id']:
            matedOccurrence = mate['matedEntities'][1]
        else:
            matedOccurrence = mate['matedEntities'][0]
        childLinkPart = getOccurrence(matedOccurrence['matedOccurrence'])
        axisFrame = np.linalg.inv(matrix)*childLinkPart['transform']
        matrix = childLinkPart['transform']
        subLink = buildRobot(child, matrix, '_'.join(childLinkPart['path']))
        robot.addJoint(link, subLink, axisFrame)

    return link

# Start building the robot
buildRobot(tree, np.matrix(np.identity(4)))
robot.finalize()
print(tree)

print("* Writing URDF file")
urdf = file('urdf/robot.urdf', 'w')
urdf.write(robot.urdf)
urdf.close()
